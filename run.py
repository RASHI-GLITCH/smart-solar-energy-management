"""
Single entry point.

    python run.py            start the dashboard at http://127.0.0.1:8000
    python run.py --seed     regenerate the simulated history and exit
"""
import sys


def main() -> None:
    if "--seed" in sys.argv:
        from backend import config as C
        from backend.simulator import generate
        df = generate(days=14, interval_s=60, inject_fault_day=11)
        df.to_csv(C.HISTORY_CSV, index=False)
        print(f"Wrote {len(df):,} rows to {C.HISTORY_CSV}")
        return

    import uvicorn
    from backend import config as C

    if not C.HISTORY_CSV.exists() and C.DATA_SOURCE == "simulator":
        from backend.simulator import generate
        print("First run: generating 14 days of simulated history ...")
        generate(days=14, interval_s=60, inject_fault_day=11).to_csv(
            C.HISTORY_CSV, index=False)

    print("\n  Smart Solar Agentic AI")
    print(f"  data source : {C.DATA_SOURCE}")
    print(f"  LLM         : {C.GROQ_MODEL} "
          f"({'key set' if C.GROQ_API_KEY else 'NO KEY -- chat disabled'})")
    print(f"  Neo4j       : {C.NEO4J_URI or 'not configured (pandas fallback)'}")
    print("\n  http://127.0.0.1:8000\n")
    uvicorn.run("backend.app:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()
