# Recovered integration run, with failures preserved

Exact tested source and tree are in source.txt. Source was clean and all 597
declared input fingerprints remained unchanged throughout this completed run.
Result: 266 PASS, 12 SKIP, 3 INCOMPLETE, 2 FAIL; overall NO-GO.

The two failures are a new map pre-dispatch guard assuming a layout field on a
ClickGUI-only extension host, and a map test fixture deleting a public startup
field that is now a descriptor after integration. No failure is hidden or
counted as PASS. Fixes are separate later commits and require a fresh aggregate.

Command:
CI=true POLAR_TEAM_CRC_REFERENCE=/workspace/shared/client-quality-external/serial-port-assistant-af97031/CRC8_CRC16.py /workspace/shared/client-quality-venv/bin/python tools/verify_all.py --profile offline --timeout 300 --json /workspace/shared/client-quality-aggregate/wave3/results.json --log-dir /workspace/shared/client-quality-aggregate/wave3/logs

Native Windows/GPU/serial/robot behavior remains unqualified.
