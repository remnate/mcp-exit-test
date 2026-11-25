# Contributing

Describe the adapter, its lifecycle trigger, the expected lifetimes and the evidence that the successor handled a call. Include a minimal synthetic reproduction and sanitized JSON. Use English for public artifacts. Contributions use the MIT license.

## Verify a candidate wheel

Start at the checkout root with the README's Node.js, npm and Codex tools on PATH. These commands install into a fresh virtual environment, run the tests outside the checkout and keep the reports in a new temporary directory.

```sh
source_dir="$PWD"
trial_dir=$(mktemp -d)
python3 -m venv "$trial_dir/venv"
export PATH="$trial_dir/venv/bin:$PATH"
cd "$trial_dir"
python -m pip wheel -w wheels "$source_dir"
python -m pip install --no-index --find-links wheels mcp-exit-test==0.1.0
python -m pip check
cp -R "$source_dir/src/mcp_exit_test/sdk" sdk
npm ci --prefix sdk --ignore-scripts --no-audit --no-fund
python -m unittest discover -s "$source_dir/tests" -v
python "$source_dir/tests/release.py" --sdk-dir sdk --out results
```

The wheelhouse includes the Python schema validator and its dependencies so the install step works offline. The release check starts an independent sentinel and verifies the kernel birth identity of each registered process after cleanup. The matrix includes real Codex and SDK runs, incomplete and malformed handshakes, valid extensions under both supported revisions, unbound and stale tool replies, a simultaneous survivor and timeout under both strict settings, and a simulated pidfd without hangup. CI builds a wheel first and runs the same checks.
