"""Eurofins KCTL RF 계측 자동 분석 & 워드 성적서 발행 대시보드.

PRD.md [STEP 1]~[STEP 5] 구현 (단일 메인 애플리케이션).
"""

import base64
import io
import json
import math
import os
import re
import zipfile
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from docxtpl import DocxTemplate
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

VISION_MODEL = "gpt-5.6-luna"          # OpenAI 5.6 Luna Vision
TEMPLATE_NAME = "report_template.docx"
SAMPLE_IMAGES = ["img_1.png", "img_2.png", "img_3.png"]
KCTL_BLUE = "#003399"
FAIL_RED = "#E03A3A"

# PRD [STEP 3] 계측 이미지의 기준 마커값 (API 미사용 오프라인 데모용)
SAMPLE_MARKERS = {
    "img_1.png": {"freq_val": 2.405, "freq_unit": "GHz", "power_val": 641.83, "power_unit": "uW"},
    "img_2.png": {"freq_val": 2.440, "freq_unit": "GHz", "power_val": 477.12, "power_unit": "uW"},
    "img_3.png": {"freq_val": 2.480, "freq_unit": "GHz", "power_val": 378.31, "power_unit": "uW"},
}

VISION_PROMPT = (
    "당신은 Agilent Swept SA 스펙트럼 분석기 화면을 판독하는 RF 계측 전문가입니다. "
    "화면 우측 상단의 녹색 'Mkr1' 마커 텍스트에서 주파수와 전력 값을 정확히 읽어 "
    "아래 JSON 스키마로만 응답하세요. 숫자는 문자열이 아닌 number 타입이어야 합니다.\n"
    '{"freq_val": 2.405095, "freq_unit": "GHz", "power_val": 641.83, "power_unit": "uW"}\n'
    "freq_unit 은 GHz 또는 MHz, power_unit 은 uW 로 표기합니다."
)


# ──────────────────────────────────────────────────────────────────────────────
# 공통 유틸
# ──────────────────────────────────────────────────────────────────────────────
def asset_path(name: str):
    """프로젝트 루트 또는 자산 하위 폴더에서 파일을 탐색한다."""
    base = Path(__file__).resolve().parent
    for candidate in (base / name, base / "kctl-rf-dashboard" / name, Path.cwd() / name):
        if candidate.exists():
            return candidate
    return None


# ──────────────────────────────────────────────────────────────────────────────
# [STEP 3] OpenAI 5.6 Luna Vision 마커 파싱 & RF 계산 엔진
# ──────────────────────────────────────────────────────────────────────────────
@st.cache_data(show_spinner=False)
def extract_marker_data(image_bytes: bytes, api_key: str) -> dict:
    """스펙트럼 분석기 이미지에서 Mkr1 마커 값을 JSON 으로 추출한다.

    이미지 바이트 기반 캐싱이므로 사이드바 슬라이더 조작 시 API 가 재호출되지 않는다.
    """
    client = OpenAI(api_key=api_key)
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    response = client.chat.completions.create(
        model=VISION_MODEL,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": VISION_PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}"},
                    },
                ],
            }
        ],
    )
    return json.loads(response.choices[0].message.content)


def compute_rf_metrics(marker: dict, cf_db: float, limit: float) -> dict:
    """마커 원시값 -> 주파수(MHz), dBm, 전계강도(dBuV/m), 마진, 판정."""
    freq_val = float(marker["freq_val"])
    freq_mhz = freq_val * 1000 if str(marker.get("freq_unit", "GHz")).upper() == "GHZ" else freq_val
    power_uw = float(marker["power_val"])

    dbm = 10 * math.log10(power_uw / 1000)
    dbuv_m = dbm + 107.0 + cf_db
    margin = limit - dbuv_m

    return {
        "freq_mhz": round(freq_mhz, 2),
        "power_uw": round(power_uw, 2),
        "dbm": round(dbm, 2),
        "dbuv_m": round(dbuv_m, 2),
        "limit": round(limit, 2),
        "margin": round(margin, 2),
        "verdict": "PASS" if margin >= 0 else "FAIL",
    }


# ──────────────────────────────────────────────────────────────────────────────
# [STEP 5] docxtpl 워드 성적서 렌더링
# ──────────────────────────────────────────────────────────────────────────────
def _loop_row(tag: str) -> str:
    """docxtpl 루프 태그만 담은 1셀짜리 표 행 XML."""
    return (
        '<w:tr><w:tc><w:tcPr><w:tcW w:w="9000" w:type="dxa"/>'
        '<w:gridSpan w:val="8"/></w:tcPr><w:p><w:r><w:t>'
        f"{tag}</w:t></w:r></w:p></w:tc></w:tr>"
    )


def load_template_stream() -> io.BytesIO:
    """report_template.docx 를 읽고 측정 결과 행에 반복 루프 태그를 주입한다.

    원본 서식에는 `{{r.*}}` 셀만 있으므로, 해당 행 앞뒤에 docxtpl 의
    `{%tr for r in rows %}` / `{%tr endfor %}` 행을 메모리상에서 삽입한다.
    디스크의 원본 템플릿 파일은 수정하지 않는다.
    """
    path = asset_path(TEMPLATE_NAME)
    if path is None:
        raise FileNotFoundError(f"{TEMPLATE_NAME} 파일을 찾을 수 없습니다.")

    with zipfile.ZipFile(io.BytesIO(path.read_bytes())) as zin:
        items = {n: zin.read(n) for n in zin.namelist()}

    xml = items["word/document.xml"].decode("utf-8")
    if "{%tr" not in xml:
        for row in re.findall(r"<w:tr[ >].*?</w:tr>", xml, flags=re.DOTALL):
            if "{{r.no}}" in row:
                xml = xml.replace(
                    row,
                    _loop_row("{%tr for r in rows %}") + row + _loop_row("{%tr endfor %}"),
                    1,
                )
                break
        items["word/document.xml"] = xml.encode("utf-8")

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, data in items.items():
            zout.writestr(name, data)
    out.seek(0)
    return out


def build_report(context: dict) -> io.BytesIO:
    """인메모리 버퍼로 워드 성적서를 렌더링한다 (디스크 임시파일 미사용)."""
    doc = DocxTemplate(load_template_stream())
    doc.render(context)
    buffer = io.BytesIO()
    doc.save(buffer)
    buffer.seek(0)          # 다운로드 전 파일 포인터 원점 복귀 (필수)
    return buffer


# ──────────────────────────────────────────────────────────────────────────────
# [STEP 2] 페이지 설정 & 사이드바 파라미터
# ──────────────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Eurofins KCTL RF Inspector", page_icon="📡", layout="wide")

with st.sidebar:
    st.title("📡 KCTL RF 계측 Inspector")
    api_key = st.text_input(
        "OpenAI API Key", type="password", value=os.getenv("OPENAI_API_KEY", "")
    )
    st.caption("AI Engine: OpenAI 5.6 Luna Vision")
    offline_demo = st.checkbox(
        "샘플 오프라인 모드 (API 미호출)",
        value=False,
        help="기본 샘플 img_1~3 의 기준 마커값으로 API 없이 파이프라인을 시연합니다.",
    )

    st.divider()
    tester = st.text_input("시험 담당자", value="홍길동 선임연구원")
    reviewer = st.text_input("기술 검토자", value="김선임 기술책임자")
    sample_name = st.text_input("시료명(EUT)", value="EUT-2026-BLE-MODULE")
    standard = st.selectbox(
        "시험 규격",
        ["FCC Part 15 Subpart B Class B (3 m)", "CISPR 32 Class B (3 m)", "KN 32"],
    )

    st.divider()
    cf_db = st.slider("안테나 보정계수 (CF, dB)", 15.0, 40.0, 28.5, 0.5)
    limit = st.slider("규격 기준치 (Limit, dBµV/m)", 120.0, 150.0, 140.0, 1.0)
    remarks = st.text_area("특이사항", value="해당 없음")

st.title("📡 Eurofins KCTL RF 계측 자동 분석 대시보드")
st.markdown(
    "스펙트럼 분석기 계측 이미지의 **Mkr1** 마커를 AI Vision 으로 판독해 "
    "방사성 방출(RE) 전계강도를 계산하고, 공식 서식의 **워드 성적서**를 즉시 발행합니다."
)

uploads = st.file_uploader(
    "계측 이미지 업로드 (다중 선택 가능)", accept_multiple_files=True, type=["png", "jpg"]
)
if st.button("📂 기본 샘플 3종(img_1~3) 일괄 불러오기"):
    loaded = []
    for name in SAMPLE_IMAGES:
        path = asset_path(name)
        if path is None:
            st.warning(f"샘플 이미지 {name} 을(를) 찾을 수 없습니다.")
            continue
        loaded.append((name, path.read_bytes()))
    st.session_state["sample_images"] = loaded
    st.session_state.pop("analysis_results", None)

# 업로드 파일이 우선, 없으면 세션에 적재된 샘플 사용
images = (
    [(f.name, f.getvalue()) for f in uploads]
    if uploads
    else st.session_state.get("sample_images", [])
)

if not images:
    st.info("이미지를 업로드하거나 위의 샘플 불러오기 버튼을 눌러 분석을 시작하세요.")
    st.stop()

if not offline_demo and not api_key:
    st.warning("사이드바에 OpenAI API Key를 입력하세요.")
    st.stop()

st.caption(f"분석 대상 {len(images)}건: " + ", ".join(name for name, _ in images))

# ──────────────────────────────────────────────────────────────────────────────
# 마커 추출(캐시) -> RF 계산(슬라이더 변경 시마다 재계산)
# ──────────────────────────────────────────────────────────────────────────────
markers = []
with st.spinner("OpenAI 5.6 Luna Vision 으로 Mkr1 마커를 판독하는 중..."):
    for idx, (name, blob) in enumerate(images, start=1):
        try:
            if offline_demo and name in SAMPLE_MARKERS:
                marker = SAMPLE_MARKERS[name]
            else:
                marker = extract_marker_data(blob, api_key)
            markers.append({"no": idx, "image": name, "marker": marker})
        except Exception as e:  # noqa: BLE001 - 앱 크래시 방지
            st.error(f"[{name}] 오류 내용: {e}")

if not markers:
    st.stop()

results = []
for item in markers:
    try:
        metrics = compute_rf_metrics(item["marker"], cf_db, limit)
    except Exception as e:  # noqa: BLE001
        st.error(f"[{item['image']}] 오류 내용: {e}")
        continue
    results.append({"no": item["no"], "image": item["image"], **metrics})

if not results:
    st.stop()

st.session_state["analysis_results"] = results

# ──────────────────────────────────────────────────────────────────────────────
# [STEP 4] KPI 카드 · Plotly 규격 비교 차트 · 판정 데이터프레임
# ──────────────────────────────────────────────────────────────────────────────
total = len(results)
pass_cnt = sum(1 for r in results if r["verdict"] == "PASS")
fail_cnt = total - pass_cnt
verdict = "PASS" if fail_cnt == 0 else "FAIL"

c1, c2, c3, c4 = st.columns(4)
c1.metric("총 측정 건수", f"{total} 건")
c2.metric("PASS", f"{pass_cnt} 건", delta=f"+{pass_cnt}", delta_color="normal")
c3.metric("FAIL", f"{fail_cnt} 건", delta=f"-{fail_cnt}" if fail_cnt else "0", delta_color="inverse")
c4.metric(
    "종합 판정",
    verdict,
    delta="규격 만족" if verdict == "PASS" else "규격 초과",
    delta_color="normal" if verdict == "PASS" else "inverse",
)

st.subheader("규격 대비 측정 전계강도")
df = pd.DataFrame(results)
fig = go.Figure()
fig.add_bar(
    x=df["freq_mhz"],
    y=df["dbuv_m"],
    marker_color=[KCTL_BLUE if v == "PASS" else FAIL_RED for v in df["verdict"]],
    text=[f"{v:.2f}" for v in df["dbuv_m"]],
    textposition="outside",
    customdata=df[["margin", "verdict", "image"]],
    hovertemplate=(
        "<b>%{customdata[2]}</b><br>"
        "주파수: %{x:.2f} MHz<br>"
        "측정값: %{y:.2f} dBµV/m<br>"
        "여유 마진: %{customdata[0]:.2f} dB<br>"
        "판정: %{customdata[1]}<extra></extra>"
    ),
    width=8,
    name="측정 전계강도",
)
fig.add_hline(
    y=limit,
    line_dash="dash",
    line_color=FAIL_RED,
    annotation_text=f"FCC Limit 기준 ({limit:.0f} dBµV/m)",
    annotation_position="top right",
)
fig.update_layout(
    xaxis_title="주파수 (MHz)",
    yaxis_title="측정 전계강도 (dBµV/m)",
    showlegend=False,
    height=460,
    margin=dict(t=40, b=40, l=60, r=30),
    hoverlabel=dict(font_size=13),
)
fig.update_yaxes(range=[0, max(limit, float(df["dbuv_m"].max())) * 1.15])
st.plotly_chart(fig, width="stretch")

st.subheader("판정 결과 상세")
table = df.rename(
    columns={
        "no": "No",
        "image": "이미지명",
        "freq_mhz": "주파수(MHz)",
        "power_uw": "측정전력(µW)",
        "dbm": "dBm",
        "dbuv_m": "측정값(dBµV/m)",
        "limit": "Limit",
        "margin": "마진(dB)",
        "verdict": "판정",
    }
)
st.dataframe(table, width="stretch", hide_index=True)

# ──────────────────────────────────────────────────────────────────────────────
# [STEP 5] 워드 성적서 다운로드
# ──────────────────────────────────────────────────────────────────────────────
st.subheader("공식 시험성적서 발행")
today = datetime.now()
context = {
    "doc_no": f"KCTL-RE-{today.strftime('%Y%m%d')}-{total:03d}",
    "test_date": today.strftime("%Y-%m-%d"),
    "test_name": "방사성 방출 (RE) 측정 결과 보고서",
    "tester": tester,
    "standard": standard,
    "sample_name": sample_name,
    "cf_db": f"{cf_db:.1f}",
    "reviewer": reviewer,
    "remarks": remarks,
    "generated_at": today.strftime("%Y-%m-%d %H:%M:%S"),
    "total": total,
    "pass_cnt": pass_cnt,
    "fail_cnt": fail_cnt,
    "verdict": verdict,
    "rows": [
        {
            "no": r["no"],
            "freq_mhz": f"{r['freq_mhz']:.2f}",
            "power_uw": f"{r['power_uw']:.2f}",
            "dbm": f"{r['dbm']:.2f}",
            "dbuv_m": f"{r['dbuv_m']:.2f}",
            "limit": f"{r['limit']:.2f}",
            "margin": f"{r['margin']:.2f}",
            "verdict": r["verdict"],
        }
        for r in results
    ],
}

try:
    buffer = build_report(context)
    st.download_button(
        label="📥 공식 시험성적서(.docx) 다운로드",
        data=buffer,
        file_name=f"KCTL_RE_Report_{today.strftime('%Y%m%d')}.docx",
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
except Exception as e:  # noqa: BLE001
    st.error(f"오류 내용: {e}")
