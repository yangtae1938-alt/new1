# microcap-radar · ZERO-BASE 미국 마이크로캡 급등 전 포착 레이더

NASDAQ·NYSE·NYSE American 전 종목을 매 실행마다 제로베이스로 스캔해, **가격이 오르기 전의 거래량 이상 신호**를 찾고
SEC 공시로 공급(희석) 위험을 교차 검증하는 스캐너입니다. 결과는 **잠정 포착**이며 매수 신호·급등 예측이 아닙니다 (투자 조언 아님).

## 핵심: 장중 시간대 보정 거래량 (Time-of-Day RVOL)
장중에 `당일 누적 거래량 ÷ 전일 전체 거래량`으로 비교하면 오전엔 항상 작게 나와 초기 신호를 놓칩니다.
이 프로젝트는 `당일 누적 ÷ 과거 N거래일의 **같은 시각까지의 누적 거래량(중앙값)**`을 기본 지표로 씁니다. (`src/radar/intraday.py`)

- 5분 버킷 누적 곡선을 날짜별로 만들어 같은 버킷 끝 시각끼리 비교 (프리마켓 04:00 포함, ET)
- 거래가 없던 분봉은 0으로 채움 · 조기 폐장일은 기준선에서 제외
- 기대 거래량 하한(`min_expected_shares`) 적용 + `denominator_small` 경고 (분모 착시 방지)
- 최초 이상 신호 시각과 그 이후 가격 변화 기록 → "거래량이 가격보다 먼저 움직였는지" 판단
- 참고용으로 전일 전체 대비, 전일 같은 시각 대비, "보통 이 시각까지 하루 거래량의 몇 %" 기반 예상 일 거래량도 출력

## 파이프라인 (깔때기)
1. **1단계 (전 종목)**: Nasdaq Trader 명단 → Polygon 전 종목 일봉(grouped, 디스크 캐시) + 전 종목 snapshot → 20/65일 평균, 일봉 엔진 A, 시총 추정(SEC frames)
2. **2단계 (상위 후보)**: Polygon 5분봉으로 같은 시각 비교 → 엔진 B·C·D
3. **3단계 (포착 종목)**: SEC EDGAR 공시 분류(공급·자본압박·이벤트 플래그) → ①~⑥ 최종 분류, 반대 증거, 감사 로그

| 엔진 | 구현 | 비고 |
|---|---|---|
| A PRE-BREAKOUT | ✅ 일봉 | 13일 거래량·가격 압축·저점 방어 |
| B VOLUME-FIRST | ✅ | 같은 시각 배수 + 가격 미반영 |
| C NO-NEWS MONSTER | ✅ | 10/30/50/100배 티어, 분모 착시 경고, SEC 48h 공시 + Polygon 뉴스 48h 확인 |
| D RE-IGNITION | ✅ 일봉+장중 | 급등 → 수축 → 재증가 |
| E/F/H 공급·자본압박·공시 | ⚠️ 양식·키워드·8-K 항목 검출 | 발행 수량·잔여 물량은 **원문 확인 필요**로 표기 |
| G 이벤트 캘린더 | ⚠️ 8-K 항목 플래그만 | D+7/D+30은 "추가 확인 필요" |

## 설치·실행
```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env        # POLYGON_API_KEY, SEC_USER_AGENT(이메일 포함) 입력
radar universe              # 적격 종목 수·제외 사유
radar scan                  # 지금 시각 기준 (장중이면 LIVE, 아니면 직전 거래일 EOD)
radar scan --asof 2026-10-07T10:30   # ET 기준 특정 시각 재현(과거 날짜 + 분봉)
radar scan --no-sec         # SEC 검증 생략
radar scan --no-news        # 뉴스 조회 생략
python run_tests.py         # 네트워크 없이 로직 테스트 (pytest도 가능)
```
결과는 `reports/`에 Markdown(후보별 표 + 감사 로그)과 JSON으로 저장됩니다. 임계값은 `config/radar.yaml`.

## 웹 화면 (Streamlit)
```bash
pip install -e ".[app]" && streamlit run app.py
```
- **Streamlit Cloud 배포**: GitHub 저장소 연결 → *Main file path* = `app.py` → Advanced settings → Secrets에 `secrets.example.toml` 형식으로 키 입력.
- 앱 주소를 아는 사람은 누구나 내 Polygon 호출을 쓸 수 있으므로 `APP_PASSWORD` 설정을 권장합니다.
- 스캔은 수 분 걸릴 수 있고, Cloud의 디스크는 재시작 시 초기화되어 일봉 캐시가 다시 쌓입니다.

## 알아둘 한계 (정직하게)
- **Float는 자동 확인 불가** → "추가 확인 필요". 회전율은 O/S 기준 참고치입니다. 시총도 SEC 커버페이지 주식 수 기반 추정입니다.
- 실시간 장중 스캔은 Polygon **유료 플랜**(실시간 snapshot, 호출 제한 없음)이 필요합니다. 무료 플랜은 지연·분당 5회 제한.
- 공시 분류는 양식·키워드 **검출**이지 검증이 아닙니다. ATM/ELOC/워런트 잔여 수량, 매도 완료 여부는 사람이 원문을 봐야 합니다.
- 뉴스는 Polygon 종목 뉴스(제목·출처·링크, 최근 48h)만 조회하며 기업 IR 사이트는 조회하지 않습니다. "무호재" = "SEC·Polygon 뉴스에서 확인된 호재 없음"일 뿐이며 뉴스가 없다는 뜻이 아닙니다. 뉴스 제목만으로 촉매 실현 가능성을 판단하지 마세요.
- 전 종목 분봉을 받지 않는 **표본형 깔때기**입니다(상위 `max_stage2`개만 2·3단계). 감사 로그에 그대로 명시됩니다.
- 조기 폐장일은 근사 규칙입니다. 거래정지 목록은 best-effort. 우선주·워런트·권리·ETF는 이름·플래그로 제외하므로 일부 누락/오분류 가능.
- 점수는 우선 검토 순서용 휴리스틱이며 백테스트되지 않았고 확률이 아닙니다 (산식: `classify.py` 상단).
- 이 코드는 샌드박스에서 **네트워크 없이** 가짜 데이터로만 테스트됐습니다. 실제 Polygon/SEC 응답 필드는 첫 실행 때 확인하세요.

## 구조
```
config/radar.yaml          임계값
app.py                     Streamlit 웹 화면
src/radar/universe.py      종목 명단·제외·거래정지
src/radar/polygon.py       전 종목 일봉/snapshot/분봉
src/radar/sec.py           EDGAR 공시·주식 수·분류
src/radar/intraday.py      ★ 같은 시각 거래량 비교
src/radar/engines.py       엔진 A~D
src/radar/classify.py      ①~⑥ 분류·점수·반대 증거
src/radar/scanner.py       깔때기 오케스트레이션·감사 로그
src/radar/report.py        Markdown/JSON 출력
```
