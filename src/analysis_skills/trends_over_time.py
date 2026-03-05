from __future__ import annotations


def run(input_data: dict) -> dict:
    years = input_data.get("years", [])
    trend = {}
    for y in years:
        trend[y] = trend.get(y, 0) + 1
    return {"skill": "trends_over_time", "counts": trend}
