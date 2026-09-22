-- QuestXPSweep
--
-- Asks the server for every quest ID in QuestV2 so that the client writes a
-- complete Cache/WDB/<locale>/questcache.wdb at logout. The cache is the
-- actual deliverable; the SavedVariables here exist to say which IDs were
-- covered, to carry titles the cache is not parsed for, and to record a
-- ground-truth turn-in log to check computed XP against.
--
-- House rules this file is written to:
--   * Nothing is registered or created until a command enables it. The one
--     frame below is unavoidable (SavedVariables do not exist before
--     ADDON_LOADED); it is reused rather than a second frame being created,
--     and it carries no registration at all once ADDON_LOADED is handled.
--   * Every setting defaults off. The sweep does not resume by itself on
--     login; /qxs start is always an explicit act.
--   * Event driven. No OnUpdate and no timers. A stalled request is cleared
--     by /qxs skip, not by a wall-clock check.
--   * Lua 5.1, ASCII only.

local addonName, ns = ...

local PREFIX = "|cff66ccffQuestXPSweep|r: "

local STATUS_OK = "ok"
local STATUS_FAIL = "fail"
local STATUS_NORESP = "noresp"

local DEFAULT_IN_FLIGHT = 10
local MAX_IN_FLIGHT = 50

local DB_VERSION = 1

-- Runtime state. None of this is saved.
local db                       -- QuestXPSweepDB, set at ADDON_LOADED
local frame                    -- see the header note on why this is one frame
local running = false
local pumping = false          -- re-entrancy guard, see Pump()
local inFlight = {}            -- questID -> true
local inFlightCount = 0
local session                  -- current entry in db.sessions, nil until start
local total = 0                -- #ns.QuestIDs, hoisted out of the pump loop

local Pump

local function Say(msg)
    print(PREFIX .. msg)
end

-- UnitLevel is a unit stat, and unit stats can come back as secret values
-- depending on restriction state. A secret value must never reach
-- SavedVariables, so drop it rather than storing it.
local function SafePlayerLevel()
    local level = UnitLevel("player")
    if issecretvalue(level) then
        return nil
    end
    return level
end

-- ---------------------------------------------------------------- recording

local function Record(questID, status)
    local entry = db.quests[questID]
    if not entry then
        entry = {}
        db.quests[questID] = entry
    end
    entry.s = status

    if status == STATUS_OK then
        -- GetTitleForQuestID is nilable: a quest whose data the server
        -- answered for can still have no discoverable title.
        entry.t = C_QuestLog.GetTitleForQuestID(questID)
        entry.l = C_QuestLog.GetQuestDifficultyLevel(questID)
    end

    if session then
        session[status] = (session[status] or 0) + 1
    end
end

-- ------------------------------------------------------------------- sweep

-- IDs in the list with no result of any kind. Not the same as "after the
-- cursor": the cursor advances when a request is dispatched, so anything that
-- was still in flight when the sweep was interrupted sits behind the cursor
-- with nothing recorded against it.
local function CountUnrecorded()
    local n = 0
    for i = 1, total do
        if db.quests[ns.QuestIDs[i]] == nil then
            n = n + 1
        end
    end
    return n
end

local function Finish()
    running = false
    frame:UnregisterEvent("QUEST_DATA_LOAD_RESULT")
    if session then
        session.stopped = GetServerTime()
    end

    local left = CountUnrecorded()
    if left > 0 then
        -- Reachable when this pass started mid-list: IDs before the starting
        -- cursor were never visited. Another /qxs start wraps and picks them up.
        Say(string.format("reached the end of the list with %d id(s) still "
            .. "unrecorded. /qxs start again to sweep those.", left))
        return
    end

    Say("sweep complete. Log out fully, then copy questcache.wdb before "
        .. "logging back in. /qxs status for counts.")
end

-- Dispatches requests until the in-flight window is full or the ID list runs
-- out. RequestLoadQuestByID can complete synchronously for data the client
-- already holds, which re-enters this function through OnEvent; the guard
-- makes the nested call a no-op and lets the outer loop carry on, because the
-- nested call has already decremented inFlightCount.
function Pump()
    if pumping then
        return
    end
    pumping = true

    local ids = ns.QuestIDs
    while running and inFlightCount < db.inFlight and db.cursor <= total do
        local id = ids[db.cursor]
        db.cursor = db.cursor + 1
        if db.quests[id] == nil then
            inFlight[id] = true
            inFlightCount = inFlightCount + 1
            if session then
                if not session.firstID then
                    session.firstID = id
                end
                session.lastID = id
            end
            C_QuestLog.RequestLoadQuestByID(id)
        end
    end

    pumping = false

    if running and inFlightCount == 0 and db.cursor > total then
        Finish()
    end
end

local function OnQuestDataLoadResult(questID, success)
    if inFlight[questID] then
        inFlight[questID] = nil
        inFlightCount = inFlightCount - 1
        Record(questID, success and STATUS_OK or STATUS_FAIL)
        Pump()
        return
    end

    -- A late answer to a request /qxs skip already gave up on. Upgrade the
    -- record; the in-flight window was released when it was skipped, so the
    -- counters must not move again.
    local entry = db.quests[questID]
    if entry and entry.s == STATUS_NORESP then
        Record(questID, success and STATUS_OK or STATUS_FAIL)
    end
end

local function StartSweep()
    if running then
        Say("already running. /qxs status, or /qxs stop.")
        return
    end
    if db.cursor > total then
        -- Wrap rather than refuse. Logging out mid-sweep leaves the in-flight
        -- IDs unrecorded with the cursor already past them, so a cursor at the
        -- end does not mean the list is covered. Pump() skips anything already
        -- recorded, so a second pass only costs the walk.
        local left = CountUnrecorded()
        if left == 0 then
            Say("every id in the list has a result. /qxs status for counts, "
                .. "or /qxs reset confirm to sweep again from scratch.")
            return
        end
        db.cursor = 1
        Say(string.format("cursor was at the end with %d id(s) unrecorded "
            .. "(interrupted requests). Wrapping to pick those up.", left))
    end

    running = true
    if not session then
        session = {
            started = GetServerTime(),
            fromIndex = db.cursor,
            level = SafePlayerLevel(),
        }
        table.insert(db.sessions, session)
    end

    frame:RegisterEvent("QUEST_DATA_LOAD_RESULT")
    Say(string.format("sweeping from index %d of %d, %d requests in flight.",
        db.cursor, total, db.inFlight))
    Pump()
end

local function StopSweep(quiet)
    if not running then
        if not quiet then
            Say("not running.")
        end
        return
    end
    running = false
    frame:UnregisterEvent("QUEST_DATA_LOAD_RESULT")
    if session then
        session.stopped = GetServerTime()
    end
    if not quiet then
        Say(string.format("stopped at index %d of %d, %d request(s) still "
            .. "unanswered.", db.cursor, total, inFlightCount))
    end
end

-- The stall escape hatch. Marks everything currently unanswered as no-response
-- and refills the window. This exists instead of a timeout because house rules
-- forbid wall-clock gates; a request that never comes back would otherwise
-- hold its slot forever and the sweep would halt with no way out.
local function SkipInFlight()
    if inFlightCount == 0 then
        Say("nothing in flight.")
        if running then
            Pump()
        end
        return
    end

    local skipped = 0
    for questID in pairs(inFlight) do
        inFlight[questID] = nil
        Record(questID, STATUS_NORESP)
        skipped = skipped + 1
    end
    inFlightCount = 0

    Say(string.format("marked %d request(s) as %s.", skipped, STATUS_NORESP))
    if running then
        Pump()
    end
end

-- ------------------------------------------------------------------ status

local function Status()
    local ok, fail, noresp, done = 0, 0, 0, 0
    for i = 1, total do
        local entry = db.quests[ns.QuestIDs[i]]
        if entry then
            done = done + 1
            if entry.s == STATUS_OK then
                ok = ok + 1
            elseif entry.s == STATUS_FAIL then
                fail = fail + 1
            elseif entry.s == STATUS_NORESP then
                noresp = noresp + 1
            end
        end
    end

    Say(string.format("%s. ids %d, done %d, remaining %d.",
        running and "running" or "idle", total, done, total - done))
    Say(string.format("  ok %d, fail %d, %s %d.", ok, fail, STATUS_NORESP, noresp))
    Say(string.format("  cursor %d, in flight %d of %d, sessions %d.",
        db.cursor, inFlightCount, db.inFlight, #db.sessions))
    Say(string.format("  verify logging %s, turn-ins recorded %d.",
        db.verify and "ON" or "off", #db.turnIns))
    if db.sourceBuild ~= db.clientBuild then
        Say(string.format("  note: id list generated from build %s, client is %s.",
            tostring(db.sourceBuild), tostring(db.clientBuild)))
    end
end

-- ------------------------------------------------- verification turn-in log

local function OnQuestTurnedIn(questID, xpReward, moneyReward)
    table.insert(db.turnIns, {
        q = questID,
        xp = xpReward,
        money = moneyReward,
        pl = SafePlayerLevel(),
        ql = C_QuestLog.GetQuestDifficultyLevel(questID),
        t = GetServerTime(),
    })
end

local function SetVerify(on)
    db.verify = on and true or false
    if db.verify then
        frame:RegisterEvent("QUEST_TURNED_IN")
        Say("verify logging ON. Turn-ins are recorded to QuestXPSweepDB.turnIns.")
    else
        frame:UnregisterEvent("QUEST_TURNED_IN")
        Say("verify logging off.")
    end
end

-- ----------------------------------------------------------------- command

local function Reset()
    StopSweep(true)
    wipe(inFlight)
    inFlightCount = 0
    session = nil
    db.quests = {}
    db.sessions = {}
    db.turnIns = {}
    db.cursor = 1
    Say("reset. All results, sessions and turn-in logs cleared.")
end

local function Usage()
    Say("commands:")
    Say("  /qxs start          begin or resume the sweep")
    Say("  /qxs stop           pause it; the cursor is kept")
    Say("  /qxs status         done / ok / fail / remaining counts")
    Say("  /qxs skip           mark unanswered requests no-response, continue")
    Say("  /qxs inflight <n>   requests in flight at once (default "
        .. DEFAULT_IN_FLIGHT .. ", max " .. MAX_IN_FLIGHT .. ")")
    Say("  /qxs verify on|off  log QUEST_TURNED_IN rewards (default off)")
    Say("  /qxs reset confirm  clear everything and start over")
end

local function HandleCommand(msg)
    local cmd, rest = string.match(strtrim(msg or ""), "^(%S*)%s*(.-)$")
    cmd = string.lower(cmd or "")
    rest = strtrim(rest or "")

    if cmd == "start" then
        StartSweep()
    elseif cmd == "stop" then
        StopSweep(false)
    elseif cmd == "status" then
        Status()
    elseif cmd == "skip" then
        SkipInFlight()
    elseif cmd == "inflight" then
        local n = tonumber(rest)
        if not n or n < 1 or n > MAX_IN_FLIGHT then
            Say("usage: /qxs inflight <1-" .. MAX_IN_FLIGHT .. ">. Currently "
                .. db.inFlight .. ".")
        else
            db.inFlight = math.floor(n)
            Say("in-flight window set to " .. db.inFlight
                .. ". Takes effect on the next request.")
            if running then
                Pump()
            end
        end
    elseif cmd == "verify" then
        if string.lower(rest) == "on" then
            SetVerify(true)
        elseif string.lower(rest) == "off" then
            SetVerify(false)
        else
            Say("usage: /qxs verify on|off. Currently "
                .. (db.verify and "ON" or "off") .. ".")
        end
    elseif cmd == "reset" then
        -- No popup: house rules forbid StaticPopup_Show, and this addon has no
        -- dependency to borrow a confirm dialog from. A typed word is enough.
        if string.lower(rest) == "confirm" then
            Reset()
        else
            Say("this clears every result, session and turn-in log. "
                .. "Type /qxs reset confirm if you mean it.")
        end
    else
        Usage()
    end
end

-- -------------------------------------------------------------- bootstrap

local function Init()
    QuestXPSweepDB = QuestXPSweepDB or {}
    db = QuestXPSweepDB

    db.version = db.version or DB_VERSION
    db.quests = db.quests or {}
    db.sessions = db.sessions or {}
    db.turnIns = db.turnIns or {}
    db.cursor = db.cursor or 1
    db.inFlight = db.inFlight or DEFAULT_IN_FLIGHT
    if db.verify == nil then
        db.verify = false
    end

    db.sourceBuild = ns.questIdSourceBuild
    local _, buildNumber = GetBuildInfo()
    db.clientBuild = buildNumber

    total = #ns.QuestIDs

    SLASH_QUESTXPSWEEP1 = "/qxs"
    SLASH_QUESTXPSWEEP2 = "/questxpsweep"
    SlashCmdList.QUESTXPSWEEP = HandleCommand

    -- An opted-in setting, so honouring it on login is not a behaviour change.
    if db.verify then
        frame:RegisterEvent("QUEST_TURNED_IN")
    end
end

local handlers = {}

function handlers.ADDON_LOADED(loadedName)
    if loadedName ~= addonName then
        return
    end
    frame:UnregisterEvent("ADDON_LOADED")
    Init()
end

handlers.QUEST_DATA_LOAD_RESULT = OnQuestDataLoadResult
handlers.QUEST_TURNED_IN = OnQuestTurnedIn

frame = CreateFrame("Frame")
frame:SetScript("OnEvent", function(_, event, ...)
    local handler = handlers[event]
    if handler then
        handler(...)
    end
end)
frame:RegisterEvent("ADDON_LOADED")
