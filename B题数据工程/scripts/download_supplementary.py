"""Download catalog-verified vehicle-class and validation sources."""
import concurrent.futures
import json
from download_sources import ROOT, download


def main():
    source = "https://resource.data.one.gov.hk/td/traffic-detectors/volByVClass-all.xml"
    jobs = [
        ("raw/vehicle_classes/detector_locations.csv", "https://static.data.gov.hk/td/traffic-atc-veh-class/info/traffic_prop_vehicle_class_info.csv", None),
        ("raw/vehicle_classes/latest.xml", source, None),
        ("raw/vehicle_classes/20261015.zip", "https://app.data.gov.hk/v1/historical-archive/get-file", {"url": source, "time": "20261015"}),
        ("raw/vehicle_classes/20261016.zip", "https://app.data.gov.hk/v1/historical-archive/get-file", {"url": source, "time": "20261016"}),
        ("raw/vehicle_classes/archive_index.json", "https://app.data.gov.hk/v1/historical-archive/list-file-versions", {"url": source, "start": "20261015", "end": "20261016"}),
        ("raw/vehicle_classes/data_specification.pdf", "https://app.data.gov.hk/v1/historical-archive/get-data-dictionary", {"url": source, "date": "20240418"}),
        ("raw/census/traffic_data.zip", "https://static.data.gov.hk/td/traffic-flow-census/ATC_TRAFFIC_DATA.zip", None),
        ("raw/census/station_points.gml", "https://static.data.gov.hk/td/traffic-flow-census/ATC_STATION_PT.gml", None),
        ("raw/journey_time/am_kowloon_2025.csv", "https://www.td.gov.hk/datagovhk_td/car-journey-time-data/resources/am_peak_car_journey_time_(cross-district_routes_in_kowloon)_2025.csv", None),
        ("raw/journey_time/pm_kowloon_2025.csv", "https://www.td.gov.hk/datagovhk_td/car-journey-time-data/resources/pm_peak_car_journey_time_(cross-district_routes_in_kowloon)_2025.csv", None),
        ("raw/traffic_news_latest.xml", "https://www.td.gov.hk/sc/special_news/trafficnews.xml", None),
        ("raw/official_gis/revision_cutoff.csv", "https://static.data.gov.hk/td/road-network-v2/DATA_LAST_UPDATED_DATE.csv", None),
    ]
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(lambda item: download(*item), jobs))
    (ROOT / "sources_supplementary.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": sum(bool(x.get("success")) for x in results), "total": len(results)}), flush=True)


if __name__ == "__main__":
    main()
