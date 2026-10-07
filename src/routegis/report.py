from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any


GEOMETRY_POLICY = "AOI 교차 객체를 원형 보존하며 AOI 밖 Geometry가 함께 보일 수 있습니다."


def write_reports(directory: Path, payload: dict[str, Any]) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / "processing_report.json"
    html_path = directory / "processing_report.html"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    def layer_row(layer: dict[str, Any]) -> str:
        provider = str(layer["provider"])
        publisher = str(layer["publisher"])
        attribution = "© OpenStreetMap contributors" if provider == "osm" else publisher
        license_url = html.escape(str(layer["license_url"]), quote=True)
        failed_bbox = layer.get("failed_tile_bbox")
        bbox_text = "" if failed_bbox is None else ", ".join(str(value) for value in failed_bbox)
        return (
            "<tr>"
            f"<td>{html.escape(str(layer['id']))}</td>"
            f"<td>{html.escape(str(layer['name']))}</td>"
            f"<td>{html.escape(provider)}</td>"
            f"<td>{html.escape(str(layer['data_id']))}</td>"
            f"<td>{html.escape(str(layer['dataset_id']))}</td>"
            f"<td>{html.escape(publisher)}<br><small>{html.escape(attribution)}</small></td>"
            f"<td>{html.escape(str(layer['license_name']))}<br>"
            f"<a href=\"{license_url}\">{license_url}</a></td>"
            f"<td class=\"{html.escape(str(layer['status']).lower())}\">"
            f"{html.escape(str(layer['status']))}</td>"
            f"<td>{int(layer['feature_count']):,}</td>"
            f"<td>{int(layer['initial_tile_count']):,}</td>"
            f"<td>{int(layer['request_count']):,}</td>"
            f"<td>{int(layer['successful_tile_count']):,}</td>"
            f"<td>{int(layer['failed_tile_count']):,}</td>"
            f"<td>{html.escape(bbox_text)}</td>"
            f"<td>{html.escape(str(layer.get('error') or ''))}</td>"
            "</tr>"
        )

    layer_rows = "".join(layer_row(layer) for layer in payload.get("layers", []))
    issue_rows = "".join(
        "<li>"
        f"<strong>{html.escape(str(issue['severity']).upper())}</strong> "
        f"{html.escape(str(issue['message']))}"
        "</li>"
        for issue in payload.get("issues", [])
    ) or "<li>검증 경고 없음</li>"
    document = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(str(payload['project']))} 처리 결과</title>
  <style>
    body {{ margin: 0; background: #f2f0e8; color: #1f2a2e; font: 15px/1.55 'Malgun Gothic', sans-serif; }}
    main {{ max-width: 1400px; margin: 40px auto; background: #fff; border-top: 8px solid #b4312c; padding: 36px; box-shadow: 0 12px 36px #1f2a2e1c; }}
    h1 {{ margin: 0 0 4px; font-size: 30px; }} h2 {{ margin-top: 32px; font-size: 18px; border-bottom: 1px solid #d8d4c8; padding-bottom: 8px; }}
    .status {{ display: inline-block; margin: 8px 0 20px; padding: 5px 10px; background: #1f2a2e; color: #fff; letter-spacing: .04em; }}
    dl {{ display: grid; grid-template-columns: 160px 1fr; gap: 7px 16px; }} dt {{ color: #687579; }} dd {{ margin: 0; font-weight: 600; }}
    .table-wrap {{ overflow-x: auto; }} table {{ width: 100%; border-collapse: collapse; }} th,td {{ padding: 9px; border-bottom: 1px solid #e6e2d7; text-align: left; vertical-align: top; white-space: nowrap; }}
    th {{ background: #f6f4ed; }} .failed,.error {{ color: #b4312c; font-weight: 700; }} .success {{ color: #276844; font-weight: 700; }}
  </style>
</head>
<body><main>
  <h1>RouteGIS AutoBuilder</h1>
  <div class="status">{html.escape(str(payload['status']))}</div>
  <dl>
    <dt>사업명</dt><dd>{html.escape(str(payload['project']))}</dd>
    <dt>원본 도면</dt><dd>{html.escape(str(payload['source']))}</dd>
    <dt>노선 길이</dt><dd>{float(payload['route_length_m']) / 1000:.3f} km</dd>
    <dt>노선 좌표계</dt><dd>{html.escape(str(payload['crs']))}</dd>
    <dt>분석 범위</dt><dd>{float(payload['buffer_m']):,.0f} m</dd>
    <dt>AOI 면적</dt><dd>{float(payload['aoi_area_m2']) / 1_000_000:.3f} km²</dd>
  </dl>
  <p>{html.escape(str(payload['geometry_policy']))}</p>
  <h2>공간정보 수집 결과</h2>
  <div class="table-wrap"><table><thead><tr><th>ID</th><th>레이어</th><th>공급자</th><th>원본 ID</th><th>데이터셋 ID</th><th>발행기관·저작자표시</th><th>라이선스</th><th>상태</th><th>객체 수</th><th>초기 타일</th><th>요청 수</th><th>성공 타일</th><th>실패 타일</th><th>실패 타일 BBOX</th><th>메시지</th></tr></thead><tbody>{layer_rows}</tbody></table></div>
  <h2>자동 검증</h2><ul>{issue_rows}</ul>
  <h2>생성 파일</h2><ul>{''.join(f'<li>{html.escape(str(item))}</li>' for item in payload.get('outputs', []))}</ul>
</main></body></html>"""
    html_path.write_text(document, encoding="utf-8")
    return html_path, json_path
