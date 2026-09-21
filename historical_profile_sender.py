"""Send historical A-I profile messages for core-timeframe scan hits.

Only the supported 1H, 4H, 1D and 1W profiles are accepted. Historical
statistics are descriptive and are not recommendations.
"""

from __future__ import annotations

import argparse
import html
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from telegram_sender import get_telegram_sender


PERIODS = ("1H", "4H", "1D", "1W")
PERIOD_ORDER = {period: index for index, period in enumerate(PERIODS)}
GROUP_TO_CODE = {
    "macd_cross": "A",
    "h8": "B",
    "i9": "C",
    "ema": "D",
    "rsi_macd": "E",
    "new": "F",
    "full": "G",
    "smi": "H",
    "rsi": "I",
}
STRATEGY_TO_CODE = {
    "macd_cross": "A",
    "h8": "B",
    "i9": "C",
    "ema": "D",
    "rsi_macd": "E",
    "new_scan": "F",
    "rsi": "I",
}
MIN_RANK_EVENTS = 8


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _hits_from_results(paths: list[str]) -> list[dict[str, str]]:
    hits: list[dict[str, str]] = []
    for path in paths:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        result_list = payload if isinstance(payload, list) else [payload]
        for result in result_list:
            period = str(result.get("period", ""))
            if period not in PERIOD_ORDER:
                continue
            for group, code in GROUP_TO_CODE.items():
                for item in result.get(group, []) or []:
                    symbol = item.get("symbol")
                    if symbol:
                        hits.append({"symbol": str(symbol), "period": period, "code": code})
    return hits


def _hits_from_state(state_path: str, start: str | None, end: str | None) -> list[dict[str, str]]:
    payload = json.loads(Path(state_path).read_text(encoding="utf-8"))
    start_dt = _parse_time(start)
    end_dt = _parse_time(end)
    hits: list[dict[str, str]] = []
    for event in payload.get("signal_history", []) or []:
        detected = _parse_time(event.get("detected_at"))
        if detected is None:
            continue
        if start_dt is not None and detected < start_dt:
            continue
        if end_dt is not None and detected > end_dt:
            continue
        period = str(event.get("period", ""))
        if period not in PERIOD_ORDER:
            continue
        strategy = str(event.get("strategy", ""))
        if strategy == "smi_macd":
            code = "G" if bool(event.get("is_full")) else "H"
        else:
            code = STRATEGY_TO_CODE.get(strategy)
        symbol = event.get("symbol")
        if code and symbol:
            hits.append({"symbol": str(symbol), "period": period, "code": code})
    return hits


def _dedupe(hits: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str, str]] = set()
    output: list[dict[str, str]] = []
    for hit in hits:
        if hit.get("period") not in PERIOD_ORDER:
            continue
        key = (hit["symbol"], hit["period"], hit["code"])
        if key in seen:
            continue
        seen.add(key)
        output.append(hit)
    output.sort(
        key=lambda row: (
            PERIOD_ORDER[row["period"]],
            row["code"],
            row["symbol"],
        )
    )
    return output


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _signed(value: Any, digits: int = 2) -> str:
    number = _number(value)
    if number is None:
        return "-"
    return f"{number:+.{digits}f}%"


def _stock_rank(
    profiles: dict[str, Any], symbol: str, period: str, code: str
) -> tuple[int | None, int]:
    rows: list[tuple[float, int, str, str]] = []
    for candidate_period in PERIODS:
        codes = (((profiles.get("periods") or {}).get(candidate_period) or {}).get(symbol) or {})
        for candidate_code, profile in codes.items():
            events = int(profile.get("events", 0) or 0)
            if events < MIN_RANK_EVENTS:
                continue
            rows.append(
                (
                    float(profile.get("quality_score", 0) or 0),
                    events,
                    candidate_period,
                    candidate_code,
                )
            )
    rows.sort(
        key=lambda row: (row[0], row[1], -PERIOD_ORDER.get(row[2], 99)),
        reverse=True,
    )
    for index, row in enumerate(rows, start=1):
        if row[2] == period and row[3] == code:
            return index, len(rows)
    return None, len(rows)


def _analyst_commentary(
    *,
    symbol: str,
    period: str,
    code: str,
    profile: dict[str, Any],
    best: dict[str, Any] | None,
    rank: int | None,
    rank_total: int,
) -> str:
    """Turn stored statistics into a concise, non-promotional analyst reading."""
    events = int(profile.get("events", 0) or 0)
    recent = int(profile.get("recent_3y_signals", 0) or 0)
    score = float(profile.get("quality_score", 0) or 0)
    primary = profile.get("primary") or {}
    positive = _number(primary.get("net_positive_rate_pct"))
    median_net = _number(primary.get("median_net_pct"))
    mfe = _number(primary.get("avg_mfe_pct"))
    mae = _number(primary.get("avg_mae_pct"))
    last_n = int(primary.get("last10_events", 0) or 0)
    last_success = int(primary.get("last10_success", 0) or 0)

    parts: list[str] = []
    if events < 5:
        parts.append(
            f"{code}/{period} geçmişi yalnız {events} olgun olaya dayanıyor; örneklem çok sınırlı."
        )
    elif events < MIN_RANK_EVENTS:
        parts.append(
            f"{code}/{period} için {events} olgun olay var; eğilim okunabilir ancak örneklem henüz sınırlı."
        )
    elif events < 20:
        parts.append(
            f"{code}/{period} profili {events} olgun olayla orta büyüklükte bir örnekleme sahip."
        )
    else:
        parts.append(
            f"{code}/{period} profili {events} olgun olayla görece geniş bir geçmişe dayanıyor."
        )

    if positive is not None and median_net is not None:
        if positive >= 65 and median_net > 0:
            parts.append(
                f"Net pozitiflik %{positive:.0f} ve medyan getiri {_signed(median_net)} ile tarihsel dağılım olumlu tarafa eğiliyor."
            )
        elif positive >= 55 and median_net > 0:
            parts.append(
                f"Net pozitiflik %{positive:.0f} ve {_signed(median_net)} medyan getiri ılımlı pozitif bir tarihsel eğilim gösteriyor."
            )
        elif positive >= 50 and median_net > 0:
            parts.append(
                f"Pozitif sonuçlar çoğunlukta (%{positive:.0f}), ancak {_signed(median_net)} medyan getiri avantajın sınırlı olduğunu gösteriyor."
            )
        elif median_net <= 0:
            parts.append(
                f"Net pozitiflik %{positive:.0f} ve medyan getiri {_signed(median_net)}; geçmiş dağılım belirgin üstünlük göstermiyor."
            )

    if mfe is not None and mae is not None:
        adverse = abs(mae)
        ratio = (mfe / adverse) if adverse > 0 else None
        if ratio is not None and ratio >= 2.0:
            parts.append(
                f"Lehte hareket ({_signed(mfe)}) aleyhte harekete ({_signed(mae)}) göre belirgin üstün."
            )
        elif ratio is not None and ratio >= 1.25:
            parts.append(
                f"MFE {_signed(mfe)} ve MAE {_signed(mae)} dengesi lehte, ancak anlamlı geri çekilme de görülebiliyor."
            )
        elif ratio is not None:
            parts.append(
                f"MFE {_signed(mfe)} ile MAE {_signed(mae)} birbirine yakın; oynaklık belirgin."
            )

    if last_n >= 3:
        recent_rate = 100.0 * last_success / last_n
        if recent_rate >= 70:
            parts.append(
                f"Yakın dönem profili destekliyor: son {last_n} olayın {last_success}'i net pozitif."
            )
        elif recent_rate <= 40:
            parts.append(
                f"Yakın dönem zayıf: son {last_n} olayın yalnız {last_success}'i net pozitif."
            )
        else:
            parts.append(
                f"Son {last_n} olayda {last_success} pozitif sonuç var; yakın dönem karışık."
            )
    elif recent > 0 and events > recent:
        parts.append(f"Son 3 yılda yalnız {recent} sinyal bulunması yakın dönem örneklemini sınırlıyor.")

    if rank is not None and rank_total > 0:
        percentile = rank / rank_total
        if percentile <= 0.20:
            parts.append(
                f"Hisse içi sıralamada #{rank}/{rank_total}; bu kombinasyon {symbol} için üst grupta."
            )
        elif percentile >= 0.70:
            parts.append(
                f"Hisse içi sıralamada #{rank}/{rank_total}; {symbol} geçmişinde daha güçlü kombinasyonlar var."
            )

    if best:
        best_code = str(best.get("code", "-"))
        best_period = str(best.get("period", "-"))
        best_score = float(best.get("quality_score", 0) or 0)
        if best_code == code and best_period == period:
            parts.append(
                f"Bu aynı zamanda {symbol} için yeterli örneklemli en güçlü tarihsel profil ({best_score:.0f}/100)."
            )
        elif best_score >= score + 10:
            parts.append(
                f"Hissenin daha güçlü tarihsel profili {best_code}/{best_period} ({best_score:.0f}/100)."
            )
        else:
            parts.append(
                f"Hissenin en güçlü tarihsel profili {best_code}/{best_period} ({best_score:.0f}/100); mevcut profil buna yakın fakat lider değil."
            )

    return " ".join(parts[:5])


def _profile_lines(hit: dict[str, str], profiles: dict[str, Any]) -> list[str]:
    symbol, period, code = hit["symbol"], hit["period"], hit["code"]
    profile = (((profiles.get("periods") or {}).get(period) or {}).get(symbol) or {}).get(code)
    best = (profiles.get("best_by_symbol") or {}).get(symbol)

    title = f"<b>{html.escape(symbol)} · {html.escape(code)} · {html.escape(period)}</b>"
    if profile is None:
        lines = [title, "Geçmiş profil için yeterli kayıt yok."]
    else:
        score = float(profile.get("quality_score", 0) or 0)
        label = html.escape(str(profile.get("quality_label", "-")))
        events = int(profile.get("events", 0) or 0)
        recent = int(profile.get("recent_3y_signals", 0) or 0)
        primary_horizon = int(profile.get("primary_horizon", 0) or 0)
        primary = profile.get("primary") or {}
        net_positive = primary.get("net_positive_rate_pct")
        median_net = primary.get("median_net_pct")
        mfe = primary.get("avg_mfe_pct")
        mae = primary.get("avg_mae_pct")
        last10_events = int(primary.get("last10_events", 0) or 0)
        last10_success = int(primary.get("last10_success", 0) or 0)
        confidence = html.escape(str(profile.get("confidence", "-")))
        rank, rank_total = _stock_rank(profiles, symbol, period, code)

        title += f" — {score:.0f}/100 · <b>{label}</b>"
        lines = [title]
        if events:
            success_text = "-" if net_positive is None else f"%{float(net_positive):.0f}"
            lines.append(
                f"Geçmiş: <b>{events}</b> olgun olay · son 3y sinyal {recent} · +{primary_horizon} bar net pozitif {success_text}"
            )
            lines.append(
                f"Medyan net {_signed(median_net)} · MFE {_signed(mfe)} · MAE {_signed(mae)} · güven {confidence}"
            )
            if last10_events:
                lines.append(f"Son {last10_events}: <b>{last10_success}/{last10_events}</b> net pozitif")
            if rank is not None:
                lines.append(f"Hisse içi tarihsel sıra: <b>#{rank}/{rank_total}</b>")

            commentary = _analyst_commentary(
                symbol=symbol,
                period=period,
                code=code,
                profile=profile,
                best=best,
                rank=rank,
                rank_total=rank_total,
            )
            if commentary:
                lines.append(f"\n<b>Analist değerlendirmesi:</b> {html.escape(commentary)}")
        else:
            lines.append("Olgunlaşmış ileri-performans örneği henüz yok.")

    if best:
        lines.append(
            f"En güçlü tarihsel profil: <b>{html.escape(str(best['code']))} · {html.escape(str(best['period']))} · {float(best['quality_score']):.0f}/100</b>"
        )
    return lines


def build_messages(
    hits: list[dict[str, str]], profiles: dict[str, Any], max_items: int = 3
) -> list[str]:
    messages: list[str] = []
    grouped: dict[str, list[dict[str, str]]] = {}
    for hit in _dedupe(hits):
        grouped.setdefault(hit["period"], []).append(hit)

    for period in sorted(grouped, key=lambda value: PERIOD_ORDER[value]):
        items = grouped[period]
        for start in range(0, len(items), max_items):
            chunk = items[start : start + max_items]
            body = [f"📚 <b>TARİHSEL PROFİL · {html.escape(period)}</b>"]
            for hit in chunk:
                body.append("\n" + "\n".join(_profile_lines(hit, profiles)))
            body.append(
                "\n<i>Getiriler sonraki mum açılışından ölçülür; %0,20 tur maliyeti düşülür. Geçmiş performans geleceği garanti etmez.</i>"
            )
            messages.append("\n".join(body))
    return messages


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profiles", required=True)
    parser.add_argument("--scan-results", nargs="*", default=[])
    parser.add_argument("--state")
    parser.add_argument("--run-start")
    parser.add_argument("--run-end")
    parser.add_argument("--output")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    profiles = json.loads(Path(args.profiles).read_text(encoding="utf-8"))
    hits = _hits_from_results(args.scan_results) if args.scan_results else []
    if not hits and args.state:
        hits = _hits_from_state(args.state, args.run_start, args.run_end)
    hits = _dedupe(hits)
    messages = build_messages(hits, profiles)

    if args.output:
        destination = Path(args.output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps({"hits": hits, "messages": messages}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    print(json.dumps({"hits": len(hits), "messages": len(messages)}, ensure_ascii=False))
    if args.dry_run:
        for message in messages:
            print("\n---\n" + message)
        return 0

    sender = get_telegram_sender()
    failures = 0
    for message in messages:
        if not sender.send_message(message):
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
