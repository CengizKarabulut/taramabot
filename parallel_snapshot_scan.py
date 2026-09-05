"""SQLite snapshot uzerinde periyot taramalarini ayri sureclerde paralel calistir."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from state_merge import load_state, merge_states, write_state


PERIODS = ("15m", "30m", "45m", "1H", "2H", "4H", "1D", "1W", "1M")
FALSE_VALUES = {"", "0", "false", "no", "off"}


def telegram_enabled() -> bool:
    return os.getenv("DISABLE_TELEGRAM", "").strip().lower() in FALSE_VALUES


def period_slug(period: str) -> str:
    return period.lower().replace("m", "min").replace("h", "hour")


def prepare_state(base_state: Path, target: Path) -> None:
    if base_state.exists():
        shutil.copy2(base_state, target)
    else:
        target.write_text("{}\n", encoding="utf-8")


def _send_historical_profiles(repository: Path, result_paths: list[str]) -> None:
    """Send optional A-I stock-history cards after the normal scan summary."""
    profile_value = os.getenv("HISTORICAL_PROFILE_FILE", "").strip()
    if not profile_value:
        print("Tarihsel profil dosyasi tanimli degil; profil follow-up atlandi.")
        return

    profile_path = Path(profile_value)
    if not profile_path.is_absolute():
        profile_path = (repository / profile_path).resolve()
    if not profile_path.is_file() or profile_path.stat().st_size == 0:
        print(f"Tarihsel profil artifact'i bulunamadi: {profile_path}; follow-up atlandi.")
        return

    command = [
        sys.executable,
        "historical_profile_sender.py",
        "--profiles",
        str(profile_path),
        "--scan-results",
        *result_paths,
    ]
    audit_value = os.getenv("HISTORICAL_PROFILE_AUDIT_FILE", "").strip()
    if audit_value:
        audit_path = Path(audit_value)
        if not audit_path.is_absolute():
            audit_path = (repository / audit_path).resolve()
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        command.extend(["--output", str(audit_path)])

    print("Tarama sonucu gonderildi; tarihsel hisse profilleri gonderiliyor...")
    subprocess.run(command, cwd=repository, env=os.environ.copy(), check=True)


def run_parallel(args: argparse.Namespace) -> None:
    database = args.db.resolve()
    if not database.is_file() or database.stat().st_size == 0:
        raise RuntimeError(f"Piyasa snapshot'i bulunamadi: {database}")

    repository = Path(__file__).resolve().parent
    base_state = args.state.resolve()
    send_telegram = telegram_enabled()
    use_state = not args.no_state

    with tempfile.TemporaryDirectory(prefix="taramabot-parallel-") as temp_name:
        temp_dir = Path(temp_name)
        processes = []

        for period in PERIODS:
            slug = period_slug(period)
            result_path = temp_dir / f"result-{slug}.json"
            state_path = temp_dir / f"state-{slug}.json"
            log_path = temp_dir / f"scan-{slug}.log"
            if use_state:
                prepare_state(base_state, state_path)

            worker_env = os.environ.copy()
            worker_env["MARKET_DATA_DB"] = str(database)
            worker_env["SCAN_RESULT_FILE"] = str(result_path)
            worker_env["DISABLE_TELEGRAM"] = "true"
            worker_env["SCANNER_STATE_FILE"] = str(state_path)

            command = [sys.executable, "main.py", "scan", period, args.market]
            if not use_state:
                command.append("--nostate")
            log_file = log_path.open("w", encoding="utf-8")
            process = subprocess.Popen(
                command,
                cwd=repository,
                env=worker_env,
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )
            processes.append(
                {
                    "period": period,
                    "process": process,
                    "log_file": log_file,
                    "log_path": log_path,
                    "result_path": result_path,
                    "state_path": state_path,
                }
            )

        failures = []
        for worker in processes:
            worker["return_code"] = worker["process"].wait()

        for worker in processes:
            worker["log_file"].close()
            print(f"\n===== {worker['period']} tarama logu =====")
            log_text = worker["log_path"].read_text(encoding="utf-8", errors="replace")
            output_encoding = sys.stdout.encoding or "utf-8"
            safe_log_text = log_text.encode(output_encoding, errors="replace").decode(output_encoding)
            print(safe_log_text)
            if worker["return_code"] != 0 or not worker["result_path"].is_file():
                failures.append(f"{worker['period']} (kod={worker['return_code']})")

        if failures:
            raise RuntimeError("Paralel tarama basarisiz: " + ", ".join(failures))

        if use_state:
            merged = load_state(base_state)
            for worker in processes:
                merged = merge_states(merged, load_state(worker["state_path"]))
            write_state(base_state, merged)

        result_paths = [str(worker["result_path"]) for worker in processes]
        summary_path = args.summary.resolve()
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(
            json.dumps(
                [json.loads(Path(path).read_text(encoding="utf-8")) for path in result_paths],
                ensure_ascii=False,
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )

        if send_telegram:
            # 1) Mevcut tarama sonucu ve ortak sinyal ozeti.
            subprocess.run(
                [sys.executable, "main.py", "summary", *result_paths],
                cwd=repository,
                env=os.environ.copy(),
                check=True,
            )
            # 2) Ayni turda cikan hisselerin tarihsel profili.
            # Karar Paneli workflow'un sonraki adiminda calistigi icin mesaj sirasi:
            # Tarama sonucu -> Tarihsel profil -> Karar Paneli.
            _send_historical_profiles(repository, result_paths)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--market", default="bist")
    parser.add_argument("--state", default=Path("state.json"), type=Path)
    parser.add_argument("--summary", default=Path("data/latest_scan_results.json"), type=Path)
    parser.add_argument("--no-state", action="store_true")
    run_parallel(parser.parse_args())


if __name__ == "__main__":
    main()
