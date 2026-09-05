"""Send per-stock historical profile messages for scan hits.

Only external A-I codes are shown. Profiles are descriptive fixed-horizon
history, not recommendations; 15m is explicitly treated as early warning.
Each card also contains a deterministic analyst-style interpretation derived
only from the stored historical metrics.
"""

from __future__ import annotations

import argparse
import html
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from telegram_sender import get_telegram_sender


PERIODS = ("15m", "30m", "45m", "1H", "2H", "4H", "1D", "1W", "1M")
PERIOD_ORDER = {period: index for index, period in enumerate(PERIODS)}
GROUP_TO_CODE = {
    "macd_cross": "A", "h8": "B", "i9": "C", "ema": "D",
    "rsi_macd": "E", "new": "F", "full": "G", "smi": "H", "rsi": "I",
}
STRATEGY_TO_CODE = {
    "macd_cross": "A", "h8": "B", "i9": "C", "ema": "D",
    "rsi_macd": "E", "new_scan": "F", "rsi": "I",
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
    hits = []
    for path in paths:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        result_list = payload if isinstance(payload, list) else [payload]
        for result in result_list:
            period = result.get("period")
            if not period:
                continue
            for group, code in GROUP_TO_CODE.items():
                for item in result.get(group, []) or []:
                    symbol = item.get("symbol")
                    if symbol:
                        hits.append({"symbol": str(symbol), "period": str(period), "code": code})
    return hits


def _hits_from_state(state_path: str, start: str | None, end: str | None) -> list[dict[str, str]]:
    payload = json.loads(Path(state_path).read_text(encoding="utf-8"))
    start_dt = _parse_time(start)
    end_dt = _parse_time(end)
    hits = []
    for event in payload.get("signal_history", []) or []:
        detected = _parse_time(event.get("detected_at"))
        if detected is None:
            continue
        if start_dt is not None and detected < start_dt:
            continue
        if end_dt is not None and detected > end_dt:
            continue
        strategy = str(event.get("strategy", ""))
        if strategy == "smi_macd":
            code = "G" if bool(event.get("is_full")) else "H"
        else:
            code = STRATEGY_TO_CODE.get(strategy)
        symbol = event.get("symbol")
        period = event.get("period")
        if code and symbol and period:
            hits.append({"symbol": str(symbol), "period": str(period), "code": code})
    return hits


def _dedupe(hits: list[dict[str, str]]) -> list[dict[str, str]]:
    seen = set()
    output = []
    for hit in hits:
        key = (hit["symbol"], hit["period"], hit["code"])
        if key in seen:
            continue
        seen.add(key)
        output.append(hit)
    output.sort(key=lambda row: (PERIOD_ORDER.get(row["period"], 99), row["code"], row["symbol"]))
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


def _stock_rank(profiles: dict[str, Any], symbol: str, period: str, code: str) -> tuple[int | None, int]:
    rows = []
    for candidate_period in PERIODS:
        if candidate_period == "15m":
            continue
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
    rows.sort(key=lambda row: (row[0], row[1], -PERIOD_ORDER.get(row[2], 99)), reverse=True)
    if period == "15m":
        return None, len(rows)
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

    # 1) Statistical reliability first: do not let attractive percentages hide small samples.
    if events < 5:
        parts.append(
            f"{code}/{period} geçmişi yalnız {events} olgun olaya dayanıyor; görünen performans olumlu olsa bile örneklem istatistiksel olarak çok sınırlı."
        )
    elif events < MIN_RANK_EVENTS:
        parts.append(
            f"{code}/{period} için {events} olgun olay bulunuyor; ilk eğilim okunabilir ancak güvenilir bir tarihsel üstünlük demek için örneklem henüz yeterince geniş değil."
        )
    elif events < 20:
        parts.append(
            f"{code}/{period} profili {events} olgun olayla orta büyüklükte bir örnekleme sahip; sonuçlar anlamlı bir eğilim veriyor fakat tek başına kesinlik taşımıyor."
        )
    else:
        parts.append(
            f"{code}/{period} profili {events} olgun olayla görece geniş bir geçmişe dayanıyor; bu nedenle istatistikler küçük örneklem profillerine göre daha anlamlı."
        )

    # 2) Direction and payoff quality.
    if positive is not None and median_net is not None:
        if positive >= 65 and median_net > 0:
            parts.append(
                f"Net pozitiflik %{positive:.0f} ve medyan getiri {_signed(median_net)} ile tarihsel dağılım belirgin biçimde olumlu tarafa eğiliyor."
            )
        elif positive >= 55 and median_net > 0:
            parts.append(
                f"Net pozitiflik %{positive:.0f} ve {_signed(median_net)} medyan getiri, ılımlı fakat pozitif bir tarihsel eğilime işaret ediyor."
            )
        elif positive >= 50 and median_net > 0:
            parts.append(
                f"Pozitif sonuçlar çoğunlukta olsa da (%{positive:.0f}), {_signed(median_net)} medyan getiri avantajın sınırlı olduğunu gösteriyor."
            )
        elif median_net <= 0:
            parts.append(
                f"Net pozitiflik %{positive:.0f} seviyesinde ve medyan getiri {_signed(median_net)}; geçmiş dağılım mevcut sinyal için belirgin bir istatistiksel üstünlük göstermiyor."
            )
        else:
            parts.append(
                f"Net pozitiflik %{positive:.0f}; sonuç dağılımı dengeli olduğundan sinyalin tek başına güçlü bir tarihsel avantaj sunduğunu söylemek zor."
            )

    # 3) Excursion balance: reward potential versus adverse movement.
    if mfe is not None and mae is not None:
        adverse = abs(mae)
        ratio = (mfe / adverse) if adverse > 0 else None
        if ratio is not None and ratio >= 2.0:
            parts.append(
                f"Lehte hareket potansiyeli ({_signed(mfe)}) aleyhte harekete ({_signed(mae)}) göre belirgin üstün; geçmişte fırsat/risk dengesi kuvvetli olmuş."
            )
        elif ratio is not None and ratio >= 1.25:
            parts.append(
                f"MFE {_signed(mfe)} ve MAE {_signed(mae)} dengesi lehte, ancak fiyatın sinyal sonrasında anlamlı geri çekilme üretebildiği de görülüyor."
            )
        elif ratio is not None:
            parts.append(
                f"MFE {_signed(mfe)} ile MAE {_signed(mae)} birbirine yakın; geçmişte getiri potansiyeline karşı oynaklık/risk belirgin olduğundan seçicilik önemli."
            )

    # 4) Recency: recent outcomes can confirm or weaken the long-run profile.
    if last_n >= 3:
        recent_rate = 100.0 * last_success / last_n
        if recent_rate >= 70:
            parts.append(
                f"Yakın dönem de profili destekliyor: son {last_n} olayın {last_success}'i net pozitif."
            )
        elif recent_rate <= 40:
            parts.append(
                f"Yakın dönem uzun vadeli tabloya göre zayıf: son {last_n} olayın yalnız {last_success}'i net pozitif."
            )
        else:
            parts.append(
                f"Son {last_n} olayda {last_success} pozitif sonuç var; yakın dönem görünümü karışık ve güçlü bir teyit üretmiyor."
            )
    elif recent > 0 and events > recent:
        parts.append(f"Son 3 yılda yalnız {recent} sinyal bulunması, yakın dönem örneklemini sınırlıyor.")

    # 5) Relative standing inside the stock and relation to the stock's best profile.
    if period == "15m":
        parts.append("15m bu sistemde işlem onayı değil, erken uyarı katmanı olarak değerlendirilmelidir.")
    elif rank is not None and rank_total > 0:
        percentile = rank / rank_total
        if percentile <= 0.20:
            parts.append(f"Hisse içi sıralamada #{rank}/{rank_total}; bu kombinasyon {symbol} için üst grupta yer alıyor.")
        elif percentile >= 0.70:
            parts.append(f"Hisse içi sıralamada #{rank}/{rank_total}; {symbol} geçmişinde daha güçlü kombinasyonlar bulunuyor.")

    if best:
        best_code = str(best.get("code", "-"))
        best_period = str(best.get("period", "-"))
        best_score = float(best.get("quality_score", 0) or 0)
        is_same = best_code == code and best_period == period
        if is_same:
            parts.append(
                f"Bu aynı zamanda {symbol} için yeterli örneklemli en güçlü tarihsel profil ({best_score:.0f}/100)."
            )
        elif best_score >= score + 10:
            parts.append(
                f"Buna karşılık hissenin daha güçlü tarihsel profili {best_code}/{best_period} ({best_score:.0f}/100); mevcut sinyal ikincil teyit niteliğinde okunmalı."
            )
        else:
            parts.append(
                f"Hissenin en güçlü tarihsel profili {best_code}/{best_period} ({best_score:.0f}/100); mevcut profil buna yakın fakat lider değil."
            )

    # Keep Telegram cards readable; the first four/five sentences carry the signal.
    return " ".join(parts[:5])


def _profile_lines(hit: dict[str, str], profiles: dict[str, Any]) -> list[str]:
    symbol, period, code = hit["symbol"], hit["period"], hit["code"]
    profile = (((profiles.get("periods") or {}).get(period) or {}).get(symbol) or {}).get(code)
    best = (profiles.get("best_by_symbol") or {}).get(symbol)
    early_best = (profiles.get("best_early_warning_by_symbol") or {}).get(symbol)

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

        if period == "15m":
            title += f" — {score:.0f}/100 · <b>ERKEN UYARI</b>"
        else:
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
        if period == "15m":
            lines.append("15m puanı işlem onayı değil; izleme/erken uyarı geçmişidir.")

    if best:
        lines.append(
            f"En güçlü tarihsel profil: <b>{html.escape(str(best['code']))} · {html.escape(str(best['period']))} · {float(best['quality_score']):.0f}/100</b>"
        )
    elif early_best:
        lines.append(
            f"En güçlü erken uyarı: <b>{html.escape(str(early_best['code']))} · 15m · {float(early_best['quality_score']):.0f}/100</b>"
        )
    return lines


def build_messages(hits: list[dict[str, str]], profiles: dict[str, Any], max_items: int = 3) -> list[str]:
    messages = []
    grouped: dict[str, list[dict[str, str]]] = {}
    for hit in _dedupe(hits):
        grouped.setdefault(hit["period"], []).append(hit)

    for period in sorted(grouped, key=lambda value: PERIOD_ORDER.get(value, 99)):
        items = grouped[period]
        for start in range(0, len(items), max_items):
            chunk = items[start : start + max_items]
            body = [f"📚 <b>TARİHSEL PROFİL · {html.escape(period)}</b>"]
            for hit in chunk:
                body.append("\n" + "\n".join(_profile_lines(hit, profiles)))
            body.append("\n<i>Getiriler sonraki mum açılışından ölçülür; %0,20 tur maliyeti düşülür. Geçmiş performans geleceği garanti etmez.</i>")
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
        destination.write_text(json.dumps({"hits": hits, "messages": messages}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

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
