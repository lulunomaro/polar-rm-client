"""Explicit SIM-only local launcher. Help/dry-run need only Python standard library."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from rm_client.sim_launcher import main
if __name__=='__main__':raise SystemExit(main(client_root=ROOT))
