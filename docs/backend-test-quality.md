# Check backend test quality

Use coverage to find missing evidence. Do not treat a coverage percentage as proof
that the assertions are useful. Use mutation testing on small, important modules to
check whether the tests reject behavior changes.

## Generate the coverage report

Run these commands from the repository root in PowerShell. The local temporary
directory avoids Windows permissions on pytest's shared system temporary folder.

```powershell
Set-Location backend
$backendTestTemp = Join-Path (Get-Location) "test-results\pytest-coverage-local"
python -m coverage erase
python -m coverage run --branch --source=app -m pytest -q --basetemp $backendTestTemp
python -m coverage report --precision=2 --show-missing --skip-covered
New-Item -ItemType Directory -Force test-results\coverage | Out-Null
python -m coverage json -o test-results\coverage\backend-coverage.json
Set-Location ..
python scripts\report_backend_debt.py `
  --coverage-file backend\.coverage `
  --output backend\test-results\coverage\backend-debt.json
```

Set the same test-only environment values that CI defines if local startup
validation requires them:

```powershell
$env:APP_KEY = "ci-test-key"
$env:ANTHROPIC_API_KEY = "ci-test-anthropic-key"
$env:SMARTROUTE_ENV = "test"
```

Never use a real provider secret for this run.

## Filter the report

Start with uncovered functions and high CRAP. Then inspect missing branches in the
public caller before writing a test.

```powershell
$debt = Get-Content backend\test-results\coverage\backend-debt.json -Raw |
  ConvertFrom-Json
$debt.crap_above_30 |
  Format-Table file,function,line,complexity,cognitive,coverage,crap -AutoSize
$debt.zero_covered |
  Sort-Object batch,file,line |
  Format-Table batch,file,function,line,complexity,cognitive -AutoSize
```

For each result, answer these questions in order:

1. Does a production caller reach it?
2. Does it own a passenger fact, authorization rule, fallback, provider boundary,
   persistence transition, or lifecycle guarantee?
3. Can a public API or module entry point reproduce the missing behavior?
4. Would the test fail if that behavior changed?

Delete proven dead code. Test important behavior through its public owner. Leave
simple generated accessors and impossible validated states alone. Do not assert a
private helper only to increase the percentage.

## Run the mutation canary

Cosmic Ray is pinned in `backend/requirements-dev.txt`. The checked-in
`backend/cosmic-ray.toml` mutates `app/services/evidence.py` and runs its focused
public tests. This is a fast canary for test strength, not a whole-backend score.

```powershell
Set-Location backend
cosmic-ray baseline cosmic-ray.toml
cosmic-ray init --force cosmic-ray.toml test-results\mutation\evidence.sqlite
cr-filter-operators test-results\mutation\evidence.sqlite cosmic-ray.toml
cosmic-ray exec cosmic-ray.toml test-results\mutation\evidence.sqlite
cr-report --surviving-only --no-show-output --show-diff `
  test-results\mutation\evidence.sqlite
```
