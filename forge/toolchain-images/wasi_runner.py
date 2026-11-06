"""Wasmtime WASI Preview 1 runner; no implicit preopens, bounded fuel/memory."""

import json
import sys
import wasmtime

path, args, preopens, fuel = sys.argv[1:]
config = wasmtime.Config()
config.consume_fuel = True
engine = wasmtime.Engine(config)
store = wasmtime.Store(engine)
store.set_fuel(int(fuel))
store.set_limits(memory_size=64 * 2**20, table_elements=100000, instances=100)
wasi = wasmtime.WasiConfig()
wasi.argv = [path, *json.loads(args)]
wasi.inherit_stdout()
wasi.inherit_stderr()
for guest, host in json.loads(preopens).items():
    wasi.preopen_dir(host, guest)
store.set_wasi(wasi)
linker = wasmtime.Linker(engine)
linker.define_wasi()
module = wasmtime.Module.from_file(engine, path)
instance = linker.instantiate(store, module)
try:
    instance.exports(store)["_start"](store)
except wasmtime.ExitTrap as exit_status:
    raise SystemExit(exit_status.code)
