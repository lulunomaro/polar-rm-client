# Startup callback lookup evidence

`lookup-red.log` was produced against extraction commit `0167a3c` by an
independent review script comparing actual old/new constructor AST regions.
It shows the original eager-lookup differences.

`lookup-green.log` was produced against corrected source commit
`607782cc2daf899bdaf573c37e399d7e7bd0ea72` by the reviewer's expanded probe:
208 comparisons of lookup/call/boolean/argument/failure-prefix behavior,
zero differences. These temporary review scripts were independently authored;
the portable permanent regression is `tests/test_startup_activation_adapter.py`.
It uses the fixed, source-referenced baseline block rather than runtime Git.

Reproduce the permanent checks with:

```sh
python -m unittest discover -s tests -p 'test_startup_activation*.py' -v
```

All 10 new tests pass, along with 96 existing focused window/assembly/lifecycle/
API tests and the API freeze mutation self-test. The static gate also passes.
This does not qualify native GL, audio, desktop visuals or physical hardware.
