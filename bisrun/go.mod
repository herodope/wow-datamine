module wowdatamine/bisrun

go 1.27.0

require (
	github.com/wowsims/forever v0.0.0
	google.golang.org/protobuf v1.36.12
)

// The sim checkout scripts/build_sim.py pins; never patched.
replace github.com/wowsims/forever => ../vendor/forever
