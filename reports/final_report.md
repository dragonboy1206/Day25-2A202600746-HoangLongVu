# Báo cáo cuối: Reliability Engineering cho Agent Gateway

## 1. Tóm tắt kiến trúc

Gateway nhận prompt, kiểm tra cache trước, sau đó gọi provider qua circuit breaker. Nếu provider chính lỗi hoặc circuit đang mở, gateway chuyển sang provider dự phòng. Nếu toàn bộ provider đều lỗi, hệ thống trả static fallback để không làm ứng dụng sập.

```text
User Request
    |
    v
[ReliabilityGateway]
    |
    +--> [ResponseCache hoặc SharedRedisCache] -- hit --> trả cached response
    |
    v miss
[CircuitBreaker: primary] --> Provider primary
    | lỗi / open
    v
[CircuitBreaker: backup]  --> Provider backup
    | lỗi / open
    v
[Static fallback message]
```

## 2. Cấu hình

| Thiết lập | Giá trị | Lý do |
|---|---:|---|
| failure_threshold | 3 | Mở circuit sau nhiều lỗi liên tiếp để tránh retry storm. |
| reset_timeout_seconds | 2.0 | Cho provider thời gian hồi phục trước khi thử probe. |
| success_threshold | 1 | Số probe thành công cần có để đóng circuit lại. |
| cache TTL | 300 giây | Giữ câu trả lời đủ lâu để tiết kiệm chi phí nhưng vẫn hạn chế dữ liệu cũ. |
| similarity_threshold | 0.92 | Ngưỡng cao để giảm nguy cơ semantic cache trả nhầm. |
| load_test requests | 100 mỗi scenario | Đủ để đo latency và fallback. |
| primary fail_rate | 0.25 | Mô phỏng provider chính không ổn định. |
| backup fail_rate | 0.05 | Mô phỏng provider dự phòng ổn định hơn. |

## 3. SLO

| SLI | SLO target | Giá trị thực tế | Đạt? |
|---|---|---:|---|
| Availability | >= 99% | 99.33% | Có |
| Latency P95 | < 2500 ms | 315.73 | Có |
| Fallback success rate | >= 95% | 97.37% | Có |
| Cache hit rate | >= 10% | 60.00% | Có |
| Recovery time | < 5000 ms | 2252.6131 | Có |

## 4. Metrics

| Metric | Value |
|---|---:|
| total_requests | 300 |
| availability | 0.9933 |
| error_rate | 0.0067 |
| latency_p50_ms | 271.72 |
| latency_p95_ms | 315.73 |
| latency_p99_ms | 318.59 |
| fallback_success_rate | 0.9737 |
| cache_hit_rate | 0.6 |
| circuit_open_count | 9 |
| recovery_time_ms | 2252.6131 |
| estimated_cost | 0.0528 |
| estimated_cost_saved | 0.18 |

## 5. So sánh cache

| Metric | Không cache | Có cache | Delta |
|---|---:|---:|---:|
| latency_p50_ms | 274.32 | 271.72 | -2.6 |
| latency_p95_ms | 316.29 | 315.73 | -0.56 |
| estimated_cost | 0.1295 | 0.0528 | -0.0767 |
| cache_hit_rate | 0 | 0.6 | 0.6 |

Nhận xét: cache làm giảm estimated_cost và giảm số lần provider phải xử lý. False-hit guard chặn trường hợp câu hỏi giống nhau nhưng khác năm hoặc số định danh.

## 6. Redis shared cache

In-memory cache chỉ nằm trong một process, nên khi triển khai nhiều instance thì mỗi instance có cache riêng và dễ lặp lại chi phí. SharedRedisCache lưu chung trong Redis, nên nhiều gateway instance có thể đọc cùng dữ liệu.

| Hạng mục | Kết quả |
|---|---|
| Code get/set Redis | Đã cài đặt HSET, HGET, EXPIRE, SCAN theo prefix. |
| Privacy guardrail | Query nhạy cảm không được lưu. |
| False-hit guardrail | Query khác năm/số định danh bị từ chối. |
| Kiểm thử Redis | `pytest tests/test_redis_cache.py -q`: 6 passed. |

### Bằng chứng shared state

| Kiểm tra | Giá trị |
|---|---|
| c2 đọc dữ liệu do c1 ghi | shared response from redis |
| similarity score | 1 |
| passed | True |

### Redis-backed chaos metrics

| Metric | Value |
|---|---:|
| total_requests | 300 |
| availability | 0.99 |
| error_rate | 0.01 |
| latency_p50_ms | 276.26 |
| latency_p95_ms | 313.35 |
| latency_p99_ms | 318.4 |
| fallback_success_rate | 0.9552 |
| cache_hit_rate | 0.6833 |
| circuit_open_count | 9 |
| recovery_time_ms | Không ghi nhận |
| estimated_cost | 0.0401 |
| estimated_cost_saved | 0.205 |

### Redis CLI output

Lệnh đã chạy: `docker compose exec -T redis redis-cli KEYS "rl:cache:*"`

```text
rl:cache:3dab98c0e49e
rl:cache:dacb2b833659
rl:cache:9e413fd814eb
rl:cache:844ef0143a5c
rl:cache:4fc3c69b9376
rl:cache:d354658dc020
rl:cache:0bc3b1acf73d
rl:cache:8baa2cfa11fa
rl:cache:734852f3cf4a
rl:cache:fff10da1c72c
rl:cache:3936614ac4c2
rl:cache:98332d0d1c9c
rl:cache:095946136fea
```

Số key trong Redis sau kiểm thử: 13.

## 7. Chaos scenarios

| Scenario | Kỳ vọng | Quan sát | Kết quả |
|---|---|---|---|
| primary_timeout_100 | Provider chính lỗi 100%, traffic chuyển sang backup. | Fallback success rate tổng: 97.37%. | pass |
| primary_flaky_50 | Circuit mở/đóng theo lỗi ngẫu nhiên, vẫn giữ availability. | Circuit open count: 9. | pass |
| all_healthy | Hầu hết request đi qua primary/cache, rất ít lỗi. | Availability tổng: 99.33%. | pass |

## 8. Phân tích điểm yếu còn lại

Điểm yếu lớn nhất là trạng thái circuit breaker vẫn nằm trong bộ nhớ từng process. Nếu chạy nhiều instance, mỗi instance có thể mở/đóng circuit khác nhau. Trước production, nên đưa counter và trạng thái circuit vào Redis hoặc một control plane chung.

## 9. Bước tiếp theo

1. Lưu circuit breaker state vào Redis để đồng bộ đa instance.
2. Thêm load test song song bằng ThreadPoolExecutor để đo khi có concurrency.
3. Thêm cảnh báo khi cache hit rate hoặc fallback success rate tụt dưới SLO.

## 10. Kết quả kiểm tra

- `pytest tests/test_redis_cache.py -q`: 6 passed.
- `pytest -q`: 35 passed, 7 xpassed.
- `ruff check src tests scripts`: All checks passed.
- `mypy src`: Success, no issues found in 8 source files.
