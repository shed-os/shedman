#!/usr/bin/env bash
# run.sh — fixture harness for StateCheckpoint in apply_core.py.
#
# Drives the in-process Python class with synthetic state-root inputs
# via SHEDOS_APPLY_STATE_ROOT. Verifies the four restore cases:
#   T1 — file existed pre-apply, mutated during apply, restored to original
#   T2 — file did NOT exist pre-apply, created during apply, removed on restore
#   T3 — file existed pre-apply, removed during apply, recreated on restore
#   T4 — successful apply: commit() is a no-op (mutations survive)

set -uo pipefail

here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "$here/../.." && pwd)
apply_core=$repo_root/packaging/shedos-system/tree/usr/lib/shedos

if [[ ! -f $apply_core/apply_core.py ]]; then
    echo "FATAL: $apply_core/apply_core.py missing" >&2
    exit 2
fi

pass=0
fail=0
failures=()

_ok() { echo "ok: $1"; ((pass++)); }
_fail() { echo "FAIL: $1: $2" >&2; failures+=("$1"); ((fail++)); }

tmp=$(mktemp -d -t shedos-apply-checkpoint-test.XXXXXX)
trap 'rm -rf -- "$tmp"' EXIT

_run_test() {
    local name=$1 script=$2
    local state_root=$tmp/$name/state
    mkdir -p "$state_root"
    if [[ -d "$tmp/$name/seed" ]]; then
        cp -a "$tmp/$name/seed/." "$state_root/"
    fi
    if PYTHONPATH=$apply_core SHEDOS_APPLY_STATE_ROOT=$state_root \
            python3 -c "$script" >"$tmp/$name.out" 2>&1; then
        if grep -q "^OK$" "$tmp/$name.out"; then
            _ok "$name"
        else
            _fail "$name" "no OK in output: $(cat "$tmp/$name.out")"
        fi
    else
        _fail "$name" "python exited non-zero: $(cat "$tmp/$name.out")"
    fi
}

# T1: existing file, mutated, restored to original content.
mkdir -p "$tmp/T1_restore_existing_file/seed"
printf '{"items": [["80", "ALLOW"]]}' > "$tmp/T1_restore_existing_file/seed/firewall.state.json"

_run_test T1_restore_existing_file '
import apply_core
cp = apply_core.StateCheckpoint()
cp.snapshot()
target = apply_core.state_root() / "firewall.state.json"
target.write_text("{\"items\": [[\"80\", \"ALLOW\"], [\"443\", \"ALLOW\"]]}")
restored = cp.restore_all()
assert len(restored) == 1, restored
assert target.read_text() == "{\"items\": [[\"80\", \"ALLOW\"]]}", target.read_text()
print("OK")
'

# T2: state file did not exist before apply; apply created it; restore removes.
_run_test T2_restore_removes_newly_created '
import apply_core
cp = apply_core.StateCheckpoint()
cp.snapshot()
target = apply_core.state_root() / "newsection.state.json"
target.write_text("{\"items\": [[\"x\", \"y\"]]}")
assert target.exists()
restored = cp.restore_all()
assert len(restored) == 1, restored
assert not target.exists(), "should have been removed"
print("OK")
'

# T3: existing file removed during apply; restore recreates with original.
mkdir -p "$tmp/T3_restore_recreates_removed/seed"
printf '{"items": [["pre-existing", "1"]]}' > "$tmp/T3_restore_recreates_removed/seed/keyring.state.json"

_run_test T3_restore_recreates_removed '
import apply_core
cp = apply_core.StateCheckpoint()
cp.snapshot()
target = apply_core.state_root() / "keyring.state.json"
target.unlink()
assert not target.exists()
restored = cp.restore_all()
assert len(restored) == 1, restored
assert target.exists(), "should have been recreated"
assert target.read_text() == "{\"items\": [[\"pre-existing\", \"1\"]]}", target.read_text()
print("OK")
'

# T4: successful apply path; commit() is a no-op; mutated content survives.
mkdir -p "$tmp/T4_commit_preserves_mutations/seed"
printf '{"items": [["80", "ALLOW"]]}' > "$tmp/T4_commit_preserves_mutations/seed/firewall.state.json"

_run_test T4_commit_preserves_mutations '
import apply_core
cp = apply_core.StateCheckpoint()
cp.snapshot()
target = apply_core.state_root() / "firewall.state.json"
target.write_text("{\"items\": [[\"80\", \"ALLOW\"], [\"443\", \"ALLOW\"]]}")
cp.commit()
assert target.read_text() == "{\"items\": [[\"80\", \"ALLOW\"], [\"443\", \"ALLOW\"]]}", \
    "mutation should survive commit"
print("OK")
'

total=$((pass + fail))
echo
echo "apply-checkpoint: $pass/$total passed"
if (( fail > 0 )); then
    printf '  %s\n' "${failures[@]}" >&2
    exit 1
fi
