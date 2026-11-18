// Command bisrun is a batch harness over the wowsims/forever sim.
//
// Batch mode reads JSON-lines jobs on stdin and writes one JSON result per line on stdout, in completion order
// (match results to jobs by Name). Each job clones the base RaidSimRequest, applies its overrides to the first
// player and the encounter, then calls core.ComputeStats and, unless statsOnly, core.RunRaidSim in-process.
//
//	bisrun -base config/presets/prot_paladin.json [-workers N] < jobs.jsonl
//	bisrun preset vendor/forever/ui/specs/paladin/protection/defaults.siml > prot_paladin.json
//
// The base request is RaidSimRequest protojson, the format upstream's `wowsimcli sim --infile` reads. `preset`
// builds that JSON from a full .siml setup the way the site's individual sim does, without the potions and conjured
// items the site adds for its APL.
//
// Known behaviour of the sim this harness does not correct:
//   - ComputeStats GearStats includes base stats plus imbues. Callers subtract an empty-gear baseline; a main hand
//     also carries the preset's weapon oil.
//   - limitCategory is not enforced: the sim equips two items of one limit category. Callers enforce it.
//   - ItemSpec.enchant is an enchant effectId; one effectId can serve several slots.
package main

import (
	"bufio"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"os"
	"runtime"
	"sync"

	"github.com/wowsims/forever/sim"
	"github.com/wowsims/forever/sim/core"
	"github.com/wowsims/forever/sim/core/proto"
	"github.com/wowsims/forever/sim/siml"
	"google.golang.org/protobuf/encoding/protojson"
	googleProto "google.golang.org/protobuf/proto"
)

const numSlots = 17

type Item struct {
	Slot    int   `json:"slot"`
	ID      int32 `json:"id"`
	Enchant int32 `json:"enchant"`
	Suffix  int32 `json:"suffix"`
}

type Job struct {
	Name string `json:"name"`
	Base string `json:"base"` // RaidSimRequest JSON path; empty = the -base file

	Gear  string `json:"gear"`  // .gear.siml text, applied before items
	Items []Item `json:"items"` // per-slot overrides
	Full  bool   `json:"full"`  // start from empty gear instead of the base's

	Bonus       map[int]float64 `json:"bonus"` // proto.Stat index -> amount, added to the base's bonus stats
	Race        string          `json:"race"`  // proto.Race name, e.g. RaceUndead
	Talents     string          `json:"talents"`
	Consumables json.RawMessage `json:"consumables"` // ConsumesSpec protojson; replaces the base's

	Targets  int     `json:"targets"`  // >0: this many copies of the first target
	TLevel   int32   `json:"tlevel"`   // first target's level; >=63 can crush, parry haste off
	TMinDmg  float64 `json:"tmindmg"`  // first target's min base melee damage
	THealth  float64 `json:"thealth"`  // first target's health
	TSchool  string  `json:"tschool"`  // proto.SpellSchool name for every target's melee, e.g. SpellSchoolShadow
	Duration float64 `json:"duration"` // fight length; variation becomes 3 s

	Iterations int32 `json:"iterations"`
	Seed       int64 `json:"seed"`
	StatsOnly  bool  `json:"statsOnly"`  // ComputeStats only, no sim
	Concurrent bool  `json:"concurrent"` // split iterations over every core (one big final sim)
	PrintGear  bool  `json:"printGear"`  // return the equipped gear as .gear.siml text
}

type Out struct {
	Name                          string
	Dps, Tps, Dtps, Tmi, Cod, Hps float64
	DpsSd, TpsSd, DtpsSd, TmiSd   float64
	Err                           string
	Final, Gear                   []float64
	Sets                          []string
	Items                         []Item `json:",omitempty"` // the gear simmed, one entry per filled slot
	Siml                          string `json:",omitempty"`
}

var (
	names     *siml.Names
	bases     = map[string]*proto.RaidSimRequest{}
	basesMu   sync.Mutex
	gearKind  siml.Kind
	defaultFn string
)

func main() {
	if len(os.Args) > 1 && os.Args[1] == "preset" {
		if len(os.Args) != 3 {
			fail("usage: bisrun preset <defaults.siml>")
		}
		preset(os.Args[2])
		return
	}
	base := flag.String("base", "", "RaidSimRequest protojson used by jobs that name no base")
	workers := flag.Int("workers", runtime.NumCPU(), "parallel jobs")
	flag.Parse()
	defaultFn = *base

	sim.RegisterAll()
	var err error
	if names, err = siml.DefaultNames(); err != nil {
		fail("siml names: " + err.Error() + " (build with -tags=with_db)")
	}
	if gearKind, err = siml.KindOf("x.gear.siml"); err != nil {
		fail(err.Error())
	}
	if defaultFn != "" {
		if _, err := loadBase(defaultFn); err != nil {
			fail(err.Error())
		}
	}

	jobs := make(chan Job)
	outs := make(chan Out)
	var wg sync.WaitGroup
	for w := 0; w < max(1, *workers); w++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for j := range jobs {
				outs <- run(j)
			}
		}()
	}
	go func() {
		sc := bufio.NewScanner(os.Stdin)
		sc.Buffer(make([]byte, 1<<20), 1<<24)
		line := 0
		for sc.Scan() {
			line++
			if len(sc.Bytes()) == 0 {
				continue
			}
			var j Job
			if err := json.Unmarshal(sc.Bytes(), &j); err != nil {
				outs <- Out{Name: fmt.Sprintf("line%d", line), Err: "bad job JSON: " + err.Error()}
				continue
			}
			jobs <- j
		}
		close(jobs)
		wg.Wait()
		close(outs)
	}()
	enc := json.NewEncoder(os.Stdout)
	for o := range outs {
		enc.Encode(o)
	}
}

func fail(msg string) {
	fmt.Fprintln(os.Stderr, "bisrun:", msg)
	os.Exit(1)
}

func loadBase(path string) (*proto.RaidSimRequest, error) {
	if path == "" {
		path = defaultFn
	}
	if path == "" {
		return nil, errors.New("job names no base and no -base was given")
	}
	basesMu.Lock()
	defer basesMu.Unlock()
	if b, ok := bases[path]; ok {
		return b, nil
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	req := &proto.RaidSimRequest{}
	if err := (protojson.UnmarshalOptions{DiscardUnknown: true}).Unmarshal(data, req); err != nil {
		return nil, fmt.Errorf("%s: %w", path, err)
	}
	if req.SimOptions == nil || req.Raid == nil || req.Encounter == nil || len(req.Encounter.Targets) == 0 {
		return nil, fmt.Errorf("%s: a base RaidSimRequest needs raid, encounter with a target, and simOptions", path)
	}
	bases[path] = req
	return req, nil
}

func parseGear(text string) (*proto.EquipmentSpec, error) {
	f, err := siml.Parse("x.gear.siml", []byte(text), names, nil)
	if err != nil {
		return nil, fmt.Errorf("gear: %w", err)
	}
	return f.Equipment(), nil
}

func run(j Job) (o Out) {
	o.Name = j.Name
	defer func() {
		if r := recover(); r != nil {
			o.Err = fmt.Sprint(r)
		}
	}()
	base, err := loadBase(j.Base)
	if err != nil {
		o.Err = err.Error()
		return
	}
	req := googleProto.Clone(base).(*proto.RaidSimRequest)
	p := req.Raid.Parties[0].Players[0]
	if p.Equipment == nil {
		p.Equipment = &proto.EquipmentSpec{}
	}
	if j.Full {
		p.Equipment.Items = nil
	}
	if j.Gear != "" {
		eq, err := parseGear(j.Gear)
		if err != nil {
			o.Err = err.Error()
			return
		}
		p.Equipment = googleProto.Clone(eq).(*proto.EquipmentSpec)
	}
	for len(p.Equipment.Items) < numSlots {
		p.Equipment.Items = append(p.Equipment.Items, &proto.ItemSpec{})
	}
	for i, it := range p.Equipment.Items {
		if it == nil {
			p.Equipment.Items[i] = &proto.ItemSpec{}
		}
	}
	for _, it := range j.Items {
		if it.Slot < 0 || it.Slot >= numSlots {
			o.Err = fmt.Sprintf("slot %d out of range", it.Slot)
			return
		}
		p.Equipment.Items[it.Slot] = &proto.ItemSpec{Id: it.ID, Enchant: it.Enchant, RandomSuffix: it.Suffix}
	}
	if j.Race != "" {
		r, ok := proto.Race_value[j.Race]
		if !ok {
			o.Err = "unknown race " + j.Race
			return
		}
		p.Race = proto.Race(r)
	}
	if j.Talents != "" {
		p.TalentsString = j.Talents
	}
	if len(j.Consumables) > 0 {
		c := &proto.ConsumesSpec{}
		if err := protojson.Unmarshal(j.Consumables, c); err != nil {
			o.Err = "consumables: " + err.Error()
			return
		}
		p.Consumables = c
	}
	if len(j.Bonus) > 0 {
		st := make([]float64, len(proto.Stat_name))
		if p.BonusStats != nil {
			copy(st, p.BonusStats.Stats)
		}
		for k, v := range j.Bonus {
			if k < 0 || k >= len(st) {
				o.Err = fmt.Sprintf("bonus stat %d out of range", k)
				return
			}
			st[k] += v
		}
		if p.BonusStats == nil {
			p.BonusStats = &proto.UnitStats{}
		}
		p.BonusStats.Stats = st
	}
	if err := applyEncounter(req.Encounter, j); err != nil {
		o.Err = err.Error()
		return
	}
	if j.Iterations > 0 {
		req.SimOptions.Iterations = j.Iterations
	}
	if j.Seed != 0 {
		req.SimOptions.RandomSeed = j.Seed
	}

	for s, it := range p.Equipment.Items {
		if it.Id != 0 {
			o.Items = append(o.Items, Item{Slot: s, ID: it.Id, Enchant: it.Enchant, Suffix: it.RandomSuffix})
		}
	}
	if j.PrintGear {
		o.Siml = string(siml.Print(gearKind, core.PlayerProtoToSpec(p),
			&proto.IndividualSimSettings{Player: &proto.Player{Equipment: p.Equipment}}, nil, names))
	}

	cs := core.ComputeStats(&proto.ComputeStatsRequest{Raid: req.Raid, Encounter: req.Encounter, SkipRotation: true})
	if cs.ErrorResult != "" {
		o.Err = cs.ErrorResult
		return
	}
	ps := cs.RaidStats.Parties[0].Players[0]
	o.Final, o.Gear, o.Sets = ps.FinalStats.Stats, ps.GearStats.Stats, ps.Sets
	if j.StatsOnly {
		return
	}
	var res *proto.RaidSimResult
	if j.Concurrent {
		res = core.RunRaidSimConcurrent(req)
	} else {
		res = core.RunRaidSim(req)
	}
	if res.Error != nil {
		o.Err = res.Error.Message
		return
	}
	m := res.RaidMetrics.Parties[0].Players[0]
	o.Dps, o.DpsSd = m.Dps.Avg, m.Dps.Stdev
	o.Tps, o.TpsSd = m.Threat.Avg, m.Threat.Stdev
	o.Dtps, o.DtpsSd = m.Dtps.Avg, m.Dtps.Stdev
	o.Tmi, o.TmiSd = m.Tmi.Avg, m.Tmi.Stdev
	o.Hps = m.Hps.Avg
	o.Cod = m.ChanceOfDeath
	return
}

func applyEncounter(enc *proto.Encounter, j Job) error {
	t0 := enc.Targets[0]
	if j.TLevel > 0 {
		t0.Level = j.TLevel
		t0.CanCrush = j.TLevel >= 63
		t0.ParryHaste = false
	}
	if j.TMinDmg > 0 {
		t0.MinBaseDamage = j.TMinDmg
	}
	if j.THealth > 0 {
		for len(t0.Stats) <= int(proto.Stat_StatHealth) {
			t0.Stats = append(t0.Stats, 0)
		}
		t0.Stats[proto.Stat_StatHealth] = j.THealth
	}
	if j.Targets > 0 {
		ts := []*proto.Target{t0}
		for i := 1; i < j.Targets; i++ {
			t := googleProto.Clone(t0).(*proto.Target)
			t.Id = t0.Id + int32(i)
			t.Name = fmt.Sprintf("%s %d", t0.Name, i+1)
			ts = append(ts, t)
		}
		enc.Targets = ts
	}
	if j.TSchool != "" {
		v, ok := proto.SpellSchool_value[j.TSchool]
		if !ok {
			return errors.New("unknown spell school " + j.TSchool)
		}
		for _, t := range enc.Targets {
			t.SpellSchool = proto.SpellSchool(v)
		}
	}
	if j.Duration > 0 {
		enc.Duration = j.Duration
		enc.DurationVariation = 3
	}
	return nil
}

// preset prints the RaidSimRequest the site's individual sim sends for a full .siml setup: the player alone in the
// first of five parties with its buffs and debuffs, against the setup's encounter. A ring enchant needs Enchanting,
// so a player without it is simmed without ring enchants, as upstream does.
func preset(path string) {
	sim.RegisterAll()
	f, err := siml.ReadFile(path)
	if err != nil {
		fail(err.Error())
	}
	s := f.Settings
	if s.GetPlayer() == nil {
		fail(path + " holds no player")
	}
	player := googleProto.Clone(s.GetPlayer()).(*proto.Player)
	if player.Rotation == nil || player.Rotation.Type != proto.APLRotation_TypeAPL {
		fmt.Fprintln(os.Stderr, "bisrun: warning: the setup's rotation is not an APL; the sim runs APL rows only")
	}
	if player.Profession1 != proto.Profession_Enchanting && player.Profession2 != proto.Profession_Enchanting {
		for _, slot := range []proto.ItemSlot{proto.ItemSlot_ItemSlotFinger1, proto.ItemSlot_ItemSlotFinger2} {
			if items := player.GetEquipment().GetItems(); int(slot) < len(items) && items[slot] != nil {
				items[slot].Enchant = 0
			}
		}
	}
	pb, rb, db := s.PartyBuffs, s.RaidBuffs, s.Debuffs
	if pb == nil {
		pb = &proto.PartyBuffs{}
	}
	if rb == nil {
		rb = &proto.RaidBuffs{}
	}
	if db == nil {
		db = &proto.Debuffs{}
	}
	raid := core.SinglePlayerRaidProto(player, pb, rb, db)
	for len(raid.Parties) < 5 {
		raid.Parties = append(raid.Parties, &proto.Party{})
	}
	raid.Tanks = s.Tanks
	raid.TargetDummies = s.TargetDummies
	if s.Encounter == nil || len(s.Encounter.Targets) == 0 {
		fail(path + " holds no encounter with a target; a preset must name its fight")
	}
	opts := &proto.SimOptions{Iterations: s.GetSettings().GetIterations(), RandomSeed: s.GetSettings().GetFixedRngSeed()}
	if opts.Iterations == 0 {
		opts.Iterations = 12500
	}
	if opts.RandomSeed == 0 {
		opts.RandomSeed = 1
	}
	req := &proto.RaidSimRequest{Raid: raid, Encounter: s.Encounter, SimOptions: opts, Type: proto.SimType_SimTypeIndividual}
	b, err := protojson.MarshalOptions{Indent: " "}.Marshal(req)
	if err != nil {
		fail(err.Error())
	}
	fmt.Println(string(b))
}
