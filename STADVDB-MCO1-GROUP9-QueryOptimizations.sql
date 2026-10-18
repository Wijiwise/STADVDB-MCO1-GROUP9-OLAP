-- Baseline Query 1
SELECT 
    DAY(COALESCE(w.departure_time, w.arrival_time, TIMESTAMP(t.service_date))) AS evt_day,
    AVG(l.arrival_delay) AS avg_arr_delay,
    AVG(w.departure_delay) AS avg_dep_delay
FROM Warehouse w
JOIN Location l ON l.service_rdt_id = w.service_rdt_id AND l.stop_rdt_id = w.stop_rdt_id
JOIN Train t    ON t.service_rdt_id = w.service_rdt_id
JOIN Stop s     ON s.stop_rdt_id    = w.stop_rdt_id
WHERE t.company = 'NS' 
  AND t.service_date BETWEEN '2024-01-01' AND '2024-01-31'
GROUP BY evt_day;

-- Baseline Query 2
SELECT 
    t.company,
    t.service_type,
    SUM(w.arrival_cancelled) / COUNT(*) * 100 AS arr_cancel_rate,
    SUM(w.departure_cancelled) / COUNT(*) * 100 AS dep_cancel_rate
FROM Warehouse w
JOIN Location l ON l.service_rdt_id = w.service_rdt_id AND l.stop_rdt_id = w.stop_rdt_id
JOIN Train t    ON t.service_rdt_id = w.service_rdt_id
WHERE t.service_date BETWEEN '2024-01-01' AND '2024-01-31'
GROUP BY t.company, t.service_type;

-- Phase 1: Single-Column Secondary Indexes
CREATE INDEX idx_train_service_date ON Train(service_date);
CREATE INDEX idx_train_company ON Train(company);
CREATE INDEX idx_location_station_name ON Location(station_name);

-- Phase 2: Composite Join-Key Indexes (Cut Query 2 time by 21.4%)
CREATE INDEX idx_warehouse_join ON Warehouse(service_rdt_id, stop_rdt_id);
CREATE INDEX idx_location_join  ON Location(service_rdt_id, stop_rdt_id);
CREATE INDEX idx_train_join     ON Train(service_rdt_id);

-- Refactored Query 1 (Optimized SQL)
SELECT 
    DAY(t.service_date) AS evt_day,
    AVG(l.arrival_delay) AS avg_arr_delay,
    AVG(w.departure_delay) AS avg_dep_delay
FROM Warehouse w
JOIN Location l ON l.service_rdt_id = w.service_rdt_id AND l.stop_rdt_id = w.stop_rdt_id
JOIN Train t    ON t.service_rdt_id = w.service_rdt_id
WHERE t.company = 'NS' 
  AND t.service_date BETWEEN '2024-01-01' AND '2024-01-31'
GROUP BY t.service_date;
