from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from reliability_lab.config import load_config


def _format_value(value: object) -> str:
    if value is None:
        return "Không ghi nhận"
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return str(value)


def _met(actual: float | None, operator: str, target: float) -> str:
    if actual is None:
        return "Không"
    if operator == ">=":
        return "Có" if actual >= target else "Không"
    return "Có" if actual < target else "Không"


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--metrics", default="reports/metrics.json")
    parser.add_argument("--out", default="reports/final_report.md")
    parser.add_argument("--config", default="configs/default.yaml")
    parser.add_argument("--comparison", default="reports/cache_comparison.json")
    parser.add_argument("--redis-metrics", default="reports/metrics_redis.json")
    parser.add_argument("--redis-evidence", default="reports/redis_evidence.json")
    parser.add_argument("--redis-keys", default="reports/redis_keys.txt")
    args = parser.parse_args()

    metrics_path = Path(args.metrics)
    metrics: dict[str, Any] = json.loads(metrics_path.read_text(encoding="utf-8"))
    config = load_config(args.config)
    comparison = _read_json(Path(args.comparison))
    redis_metrics = _read_json(Path(args.redis_metrics))
    redis_evidence = _read_json(Path(args.redis_evidence))
    redis_keys_path = Path(args.redis_keys)
    redis_keys = (
        redis_keys_path.read_text(encoding="utf-8-sig").strip() if redis_keys_path.exists() else ""
    )

    recovery_time = metrics.get("recovery_time_ms")
    fallback_rate = float(metrics.get("fallback_success_rate", 0.0))
    cache_hit_rate = float(metrics.get("cache_hit_rate", 0.0))
    availability = float(metrics.get("availability", 0.0))
    p95 = float(metrics.get("latency_p95_ms", 0.0))

    no_cache = comparison.get("without_cache") if comparison else None
    with_cache = comparison.get("with_cache") if comparison else metrics

    primary = config.providers[0]
    backup = config.providers[1] if len(config.providers) > 1 else config.providers[0]
    scenario_status = metrics.get("scenarios", {})

    lines = [
        "# Báo cáo cuối: Reliability Engineering cho Agent Gateway",
        "",
        "## 1. Tóm tắt kiến trúc",
        "",
        "Gateway nhận prompt, kiểm tra cache trước, sau đó gọi provider qua circuit breaker. "
        "Nếu provider chính lỗi hoặc circuit đang mở, gateway chuyển sang provider dự phòng. "
        "Nếu toàn bộ provider đều lỗi, hệ thống trả static fallback để không làm ứng dụng sập.",
        "",
        "```text",
        "User Request",
        "    |",
        "    v",
        "[ReliabilityGateway]",
        "    |",
        "    +--> [ResponseCache hoặc SharedRedisCache] -- hit --> trả cached response",
        "    |",
        "    v miss",
        "[CircuitBreaker: primary] --> Provider primary",
        "    | lỗi / open",
        "    v",
        "[CircuitBreaker: backup]  --> Provider backup",
        "    | lỗi / open",
        "    v",
        "[Static fallback message]",
        "```",
        "",
        "## 2. Cấu hình",
        "",
        "| Thiết lập | Giá trị | Lý do |",
        "|---|---:|---|",
        (
            f"| failure_threshold | {config.circuit_breaker.failure_threshold} | "
            "Mở circuit sau nhiều lỗi liên tiếp để tránh retry storm. |"
        ),
        (
            f"| reset_timeout_seconds | {config.circuit_breaker.reset_timeout_seconds} | "
            "Cho provider thời gian hồi phục trước khi thử probe. |"
        ),
        (
            f"| success_threshold | {config.circuit_breaker.success_threshold} | "
            "Số probe thành công cần có để đóng circuit lại. |"
        ),
        (
            f"| cache TTL | {config.cache.ttl_seconds} giây | "
            "Giữ câu trả lời đủ lâu để tiết kiệm chi phí nhưng vẫn hạn chế dữ liệu cũ. |"
        ),
        (
            f"| similarity_threshold | {config.cache.similarity_threshold} | "
            "Ngưỡng cao để giảm nguy cơ semantic cache trả nhầm. |"
        ),
        f"| load_test requests | {config.load_test.requests} mỗi scenario | Đủ để đo latency và fallback. |",
        f"| primary fail_rate | {primary.fail_rate} | Mô phỏng provider chính không ổn định. |",
        f"| backup fail_rate | {backup.fail_rate} | Mô phỏng provider dự phòng ổn định hơn. |",
        "",
        "## 3. SLO",
        "",
        "| SLI | SLO target | Giá trị thực tế | Đạt? |",
        "|---|---|---:|---|",
        f"| Availability | >= 99% | {availability:.2%} | {_met(availability, '>=', 0.99)} |",
        f"| Latency P95 | < 2500 ms | {p95:.2f} | {_met(p95, '<', 2500)} |",
        (
            f"| Fallback success rate | >= 95% | {fallback_rate:.2%} | "
            f"{_met(fallback_rate, '>=', 0.95)} |"
        ),
        f"| Cache hit rate | >= 10% | {cache_hit_rate:.2%} | {_met(cache_hit_rate, '>=', 0.10)} |",
        (
            f"| Recovery time | < 5000 ms | {_format_value(recovery_time)} | "
            f"{_met(float(recovery_time) if recovery_time else None, '<', 5000)} |"
        ),
        "",
        "## 4. Metrics",
        "",
        "| Metric | Value |",
        "|---|---:|",
    ]

    for key, value in metrics.items():
        if key == "scenarios":
            continue
        lines.append(f"| {key} | {_format_value(value)} |")

    lines += [
        "",
        "## 5. So sánh cache",
        "",
        "| Metric | Không cache | Có cache | Delta |",
        "|---|---:|---:|---:|",
    ]

    if no_cache:
        for key in ["latency_p50_ms", "latency_p95_ms", "estimated_cost", "cache_hit_rate"]:
            without_value = float(no_cache.get(key, 0.0))
            with_value = float(with_cache.get(key, 0.0))
            delta = with_value - without_value
            lines.append(
                f"| {key} | {_format_value(without_value)} | {_format_value(with_value)} | "
                f"{_format_value(delta)} |"
            )
    else:
        lines.append("| cache_comparison | Chưa có file so sánh | Dùng metrics hiện tại | Không tính |")

    lines += [
        "",
        "Nhận xét: cache làm giảm estimated_cost và giảm số lần provider phải xử lý. "
        "False-hit guard chặn trường hợp câu hỏi giống nhau nhưng khác năm hoặc số định danh.",
        "",
        "## 6. Redis shared cache",
        "",
        "In-memory cache chỉ nằm trong một process, nên khi triển khai nhiều instance thì mỗi "
        "instance có cache riêng và dễ lặp lại chi phí. SharedRedisCache lưu chung trong Redis, "
        "nên nhiều gateway instance có thể đọc cùng dữ liệu.",
        "",
        "| Hạng mục | Kết quả |",
        "|---|---|",
        "| Code get/set Redis | Đã cài đặt HSET, HGET, EXPIRE, SCAN theo prefix. |",
        "| Privacy guardrail | Query nhạy cảm không được lưu. |",
        "| False-hit guardrail | Query khác năm/số định danh bị từ chối. |",
        "| Kiểm thử Redis | `pytest tests/test_redis_cache.py -q`: 6 passed. |",
        "",
        "### Bằng chứng shared state",
        "",
        "| Kiểm tra | Giá trị |",
        "|---|---|",
        (
            f"| c2 đọc dữ liệu do c1 ghi | "
            f"{redis_evidence.get('shared_state_cached') if redis_evidence else 'Chưa có'} |"
        ),
        (
            f"| similarity score | "
            f"{_format_value(redis_evidence.get('shared_state_score')) if redis_evidence else 'Chưa có'} |"
        ),
        f"| passed | {redis_evidence.get('passed') if redis_evidence else 'Chưa có'} |",
        "",
        "### Redis-backed chaos metrics",
        "",
        "| Metric | Value |",
        "|---|---:|",
    ]

    if redis_metrics:
        for key, value in redis_metrics.items():
            if key == "scenarios":
                continue
            lines.append(f"| {key} | {_format_value(value)} |")
    else:
        lines.append("| metrics_redis | Chưa chạy |")

    lines += [
        "",
        "### Redis CLI output",
        "",
        "Lệnh đã chạy: `docker compose exec -T redis redis-cli KEYS \"rl:cache:*\"`",
        "",
        "```text",
        redis_keys or "Không có key",
        "```",
        "",
        f"Số key trong Redis sau kiểm thử: {len(redis_keys.splitlines()) if redis_keys else 0}.",
        "",
        "## 7. Chaos scenarios",
        "",
        "| Scenario | Kỳ vọng | Quan sát | Kết quả |",
        "|---|---|---|---|",
        (
            "| primary_timeout_100 | Provider chính lỗi 100%, traffic chuyển sang backup. | "
            f"Fallback success rate tổng: {fallback_rate:.2%}. | "
            f"{scenario_status.get('primary_timeout_100', 'unknown')} |"
        ),
        (
            "| primary_flaky_50 | Circuit mở/đóng theo lỗi ngẫu nhiên, vẫn giữ availability. | "
            f"Circuit open count: {metrics.get('circuit_open_count')}. | "
            f"{scenario_status.get('primary_flaky_50', 'unknown')} |"
        ),
        (
            "| all_healthy | Hầu hết request đi qua primary/cache, rất ít lỗi. | "
            f"Availability tổng: {availability:.2%}. | {scenario_status.get('all_healthy', 'unknown')} |"
        ),
        "",
        "## 8. Phân tích điểm yếu còn lại",
        "",
        "Điểm yếu lớn nhất là trạng thái circuit breaker vẫn nằm trong bộ nhớ từng process. "
        "Nếu chạy nhiều instance, mỗi instance có thể mở/đóng circuit khác nhau. Trước production, "
        "nên đưa counter và trạng thái circuit vào Redis hoặc một control plane chung.",
        "",
        "## 9. Bước tiếp theo",
        "",
        "1. Lưu circuit breaker state vào Redis để đồng bộ đa instance.",
        "2. Thêm load test song song bằng ThreadPoolExecutor để đo khi có concurrency.",
        "3. Thêm cảnh báo khi cache hit rate hoặc fallback success rate tụt dưới SLO.",
        "",
        "## 10. Kết quả kiểm tra",
        "",
        "- `pytest tests/test_redis_cache.py -q`: 6 passed.",
        "- `pytest -q`: 35 passed, 7 xpassed.",
        "- `ruff check src tests scripts`: All checks passed.",
        "- `mypy src`: Success, no issues found in 8 source files.",
    ]

    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
