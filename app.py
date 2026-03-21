import re
import google.generativeai as genai
import io
from datetime import datetime, timedelta

# --- [초기 설정 및 API 키 보안 세팅] ---
st.set_page_config(layout="wide", page_title="AI 퀀트 스캐너 & 애널리스트")
@@ -121,6 +122,18 @@ def get_recent_fin_value(soup, keyword):
except: pass
return 0.0

# [신규 추가] 신용잔고율 추출 함수
def get_credit_ratio(soup):
    try:
        for th in soup.find_all('th'):
            if '신용비율' in th.text:
                td = th.find_next_sibling('td') or th.find_parent('tr').find('td')
                if td:
                    val = re.sub(r'[^0-9.]', '', td.text)
                    if val: return float(val)
    except: pass
    return 0.0

def check_smart_money(code):
try:
url = f"https://finance.naver.com/item/frgn.naver?code={code}"
@@ -145,7 +158,7 @@ def convert_df_to_csv(df):
if 'target_rsi' not in st.session_state: st.session_state.target_rsi = 70
if 'min_price' not in st.session_state: st.session_state.min_price = 2000 
if 'scanned_data' not in st.session_state: st.session_state.scanned_data = None 
if 'watchlist' not in st.session_state: st.session_state.watchlist = [] # [신규] 관심종목 기억장치
if 'watchlist' not in st.session_state: st.session_state.watchlist = [] 

if 'use_marcap' not in st.session_state: st.session_state.use_marcap = True
if 'use_per' not in st.session_state: st.session_state.use_per = True
@@ -159,85 +172,69 @@ def convert_df_to_csv(df):
# --- [사이드바: 종목 선별 기준] ---
st.sidebar.header("🔍 1~3단계: 전체 시장 스캔 필터")

with st.sidebar.expander("❓ 용어 및 적정주가 모델 설명"):
with st.sidebar.expander("❓ 용어 및 분석 지표 설명"):
st.caption("✅ **PER/PBR/ROE**: 가치평가의 기본 3요소")
    st.caption("🎯 **S-RIM / 그레이엄**: 적정주가 산출 모델")
    st.caption("📈 **MACD / RSI**: 추세 전환 및 과매수/과매도 지표")
    st.caption("🕯️ **캔들 / 볼린저밴드**: 차트 바닥(반등) 타점 판독")

scan_limit = st.sidebar.selectbox("검사할 종목 수 (시총 상위)", [50, 100, 200, 500, 1000], index=1, help="국내 상장사 중 시가총액이 높은 순서대로 훑을 개수를 결정합니다.")
    st.caption("📈 **MACD/RSI/볼린저/캔들**: 차트 바닥(반등) 타점 판독")
    st.caption("🦅 **52주 모멘텀**: 고점 돌파를 시도하는 강한 주도주 판독")
    st.caption("💣 **신용잔고율**: 개미들의 '빚투' 비율 (8% 이상시 폭락 위험)")

scan_limit = st.sidebar.selectbox("검사할 종목 수 (시총 상위)", [50, 100, 200, 500, 1000], index=1)
df_krx_full = get_krx_data()
sectors_list = [s for s in df_krx_full['Sector'].unique() if isinstance(s, str)]
sectors_list.sort()
default_excludes = [s for s in ['금융업', '보험업'] if s in sectors_list]

excluded_sectors = st.sidebar.multiselect("🚫 제외할 업종 (가치 트랩 방어)", options=sectors_list, default=default_excludes)

with st.sidebar.expander("⚙️ 세부 재무/가격 필터 설정 (클릭하여 열기)"):
st.session_state.use_marcap = st.checkbox("✅ 최소 시가총액 적용", value=st.session_state.use_marcap)
st.session_state.min_marcap = st.number_input("시가총액 (억원)", value=st.session_state.min_marcap, step=100, disabled=not st.session_state.use_marcap)
    
st.session_state.use_min_price = st.checkbox("✅ 최소 주가 적용 (동전주 제외)", value=st.session_state.use_min_price)
st.session_state.min_price = st.number_input("최소 주가 (원)", value=st.session_state.min_price, step=500, disabled=not st.session_state.use_min_price)

st.session_state.use_per = st.checkbox("✅ 최대 PER 적용", value=st.session_state.use_per)
st.session_state.target_per = st.number_input("PER (배)", value=st.session_state.target_per, step=1, disabled=not st.session_state.use_per)

st.session_state.use_pbr = st.checkbox("✅ 최대 PBR 적용", value=st.session_state.use_pbr)
st.session_state.target_pbr = st.number_input("PBR (배)", value=st.session_state.target_pbr, step=0.1, disabled=not st.session_state.use_pbr)

st.session_state.use_roe = st.checkbox("✅ 최소 ROE 적용", value=st.session_state.use_roe)
st.session_state.min_roe = st.number_input("ROE (%)", value=st.session_state.min_roe, step=1, disabled=not st.session_state.use_roe)

st.session_state.use_debt = st.checkbox("✅ 최대 부채비율 적용", value=st.session_state.use_debt)
st.session_state.max_debt = st.number_input("부채비율 (%)", value=st.session_state.max_debt, step=10, disabled=not st.session_state.use_debt)

st.session_state.use_op = st.checkbox("✅ 영업이익 흑자(+) 유지", value=st.session_state.use_op)
    
st.session_state.use_rsi = st.checkbox("✅ 최대 RSI 적용", value=st.session_state.use_rsi)
st.session_state.target_rsi = st.number_input("RSI (14일)", value=st.session_state.target_rsi, step=1, disabled=not st.session_state.use_rsi)

scan_button = st.sidebar.button("🎯 전체 시장 스캐너 가동 (최대 5분)", use_container_width=True)

# [신규] 관심종목 다이렉트 스캔 구역
st.sidebar.divider()
st.sidebar.header("⚡ 4단계: 관심종목 쾌속 스캔")
st.sidebar.info("💡 전체 스캔 없이, 내가 고른 종목만 1초 만에 바로 분석합니다. (재무 필터 무시)")

st.session_state.watchlist = st.sidebar.multiselect(
    "장바구니 (검색하여 추가)", 
    options=df_krx_full['Name'].tolist(), 
    default=st.session_state.watchlist,
    help="삼성전자, 카카오 등을 타이핑해서 추가해두면 언제든 바로 분석할 수 있습니다."
)
st.session_state.watchlist = st.sidebar.multiselect("장바구니 (검색하여 추가)", options=df_krx_full['Name'].tolist(), default=st.session_state.watchlist)
direct_scan_button = st.sidebar.button("🚀 선택 종목 다이렉트 분석 (1초)", type="primary", use_container_width=True)

# --- [메인 화면 로직: 매크로 풍향계 & 스캔] ---
st.title("📈 AI 주식 발굴 및 심층 분석 시스템")

# [신규] 분석 기준 일시(Timestamp) 생성 (한국 시간 KST 기준)
current_time_kst = (datetime.utcnow() + timedelta(hours=9)).strftime('%Y년 %m월 %d일 %H:%M:%S')

try:
df_kospi = fdr.DataReader('KS11').tail(20)
cur_kospi = df_kospi['Close'].iloc[-1]
ma20_kospi = df_kospi['Close'].mean()
if cur_kospi >= ma20_kospi:
st.success(f"🧭 **오늘의 시장 풍향계:** 🟢 강세장 (코스피 20일선 돌파 유지 중) | 현재가: {cur_kospi:,.2f}pt\n\n💡 **AI 전략 조언:** 시장에 돈이 돌고 있습니다. RSI 70 이하의 정배열 우량주를 적극적으로 공략하세요!")
else:
        st.error(f"🧭 **오늘의 시장 풍향계:** 🔴 약세장 (코스피 20일선 이탈) | 현재가: {cur_kospi:,.2f}pt\n\n💡 **AI 전략 조언:** 시장이 조정을 받고 있습니다. 가치평가(S-RIM) 대비 매우 저렴하고 RSI가 30 근처인 종목만 보수적으로 접근하세요.")
        st.error(f"🧭 **오늘의 시장 풍향계:** 🔴 약세장 (코스피 20일선 이탈) | 현재가: {cur_kospi:,.2f}pt\n\n💡 **AI 전략 조언:** 시장이 조정을 받고 있습니다. 가치평가(S-RIM) 대비 매우 저렴하고 하락방어율이 좋은 종목만 보수적으로 접근하세요.")
except:
pass

if scan_button or direct_scan_button:
    st.session_state.scanned_data = None # 기존 데이터 초기화
    st.session_state.scanned_data = None 
df_krx = get_krx_data()

    # 1. 대상 종목 선정 로직 분기
if direct_scan_button:
if not st.session_state.watchlist:
st.warning("⚠️ 왼쪽 메뉴에서 관심종목을 먼저 추가해주세요.")
st.stop()
filtered_by_cap = df_krx[df_krx['Name'].isin(st.session_state.watchlist)]
        st.info(f"⚡ 다이렉트 스캔: 선택하신 {len(filtered_by_cap)}개 종목을 즉시 추출합니다.")
else:
with st.spinner(f'1차: 시총 상위 {scan_limit}개 종목 로드 중... (최대 5분 소요)'):
if excluded_sectors: df_krx = df_krx[~df_krx['Sector'].isin(excluded_sectors)]
@@ -248,14 +245,14 @@ def convert_df_to_csv(df):
filtered_by_cap = df_krx.sort_values('Marcap', ascending=False).head(scan_limit)

if not filtered_by_cap.empty:
        progress_text = f"2차: 네이버 금융 재무 데이터 수집 중..."
        progress_text = f"2차: 네이버 금융 재무 및 신용 데이터 수집 중..."
progress_bar = st.progress(0, text=progress_text)
fin_results = []
total_stocks = len(filtered_by_cap)

for idx, row in enumerate(filtered_by_cap.itertuples()):
code = row.Code
            url = f"https://finance.naver.com/item/main.naver?code={code}" # 크롤링은 속도/안정성 위해 기존 주소 유지
            url = f"https://finance.naver.com/item/main.naver?code={code}" 
try:
res = requests.get(url, headers={'User-agent': 'Mozilla/5.0'})
soup = BeautifulSoup(res.text, 'html.parser')
@@ -267,10 +264,13 @@ def convert_df_to_csv(df):
op_profit = get_recent_fin_value(soup, '영업이익')
current_price = getattr(row, 'Price', int(row.Marcap / float(row.Stocks)) if getattr(row, 'Stocks', 0) else 0)

                # [신규] 신용잔고율 스크래핑
                credit_ratio = get_credit_ratio(soup)
                
fin_results.append({
'Code': code, 'Name': row.Name, '업종': row.Sector, '시가총액(억)': int(row.Marcap // 100000000), '현재가': current_price,
'PER': round(per, 2), 'PBR': round(pbr, 2), 'ROE': round(roe, 2),
                    '부채비율(%)': round(debt_ratio, 2), '영업이익(억)': op_profit, '배당(%)': dvr
                    '부채비율(%)': round(debt_ratio, 2), '영업이익(억)': op_profit, '배당(%)': dvr, '신용비율(%)': credit_ratio
})
except: pass 
progress_bar.progress((idx + 1) / total_stocks, text=f"{progress_text} ({idx+1}/{total_stocks} 완료)")
@@ -280,7 +280,7 @@ def convert_df_to_csv(df):

if not df_fin.empty:
if direct_scan_button:
                survivors_df = df_fin.copy() # 다이렉트 스캔은 재무 필터 무시
                survivors_df = df_fin.copy() 
else:
mask = pd.Series(True, index=df_fin.index)
if st.session_state.use_per: mask = mask & (df_fin['PER'] > 0) & (df_fin['PER'] <= st.session_state.target_per)
@@ -305,7 +305,6 @@ def convert_df_to_csv(df):
df_price = fdr.DataReader(row_dict['Code']).tail(40) 
if not df_price.empty:
rsi_val = calculate_rsi(df_price).iloc[-1]
                        # 다이렉트 모드이거나 필터 통과시 표출
if direct_scan_button or not st.session_state.use_rsi or rsi_val <= st.session_state.target_rsi:
row_dict['RSI'] = round(rsi_val, 1)
final_results.append(row_dict)
@@ -316,194 +315,229 @@ def convert_df_to_csv(df):
if final_results: st.session_state.scanned_data = pd.DataFrame(final_results).sort_values(by='ROE', ascending=False)
else: st.session_state.scanned_data = pd.DataFrame()

# --- [메인 화면 출력: 워치리스트 및 대시보드] ---
# --- [메인 화면 출력: 탭(Tab) 기반 UI 레이아웃] ---
if st.session_state.scanned_data is not None and not st.session_state.scanned_data.empty:
final_df = st.session_state.scanned_data

    st.subheader(f"✅ 분석 결과 ({len(final_df)}개 발견)")
    display_df = final_df.copy()
    display_df['시가총액(억)'] = display_df['시가총액(억)'].apply(lambda x: f"{x:,}")
    display_df['영업이익(억)'] = display_df['영업이익(억)'].apply(lambda x: f"{int(x):,}") 
    display_df['현재가'] = display_df['현재가'].apply(lambda x: f"{int(x):,}") 
    # [신규] 분석 데이터의 신뢰도를 높여주는 Timestamp 표시
    st.caption(f"🕒 **데이터 기준 일시 (KST):** {current_time_kst}")

    display_cols = ['Code', 'Name', '업종', '시가총액(억)', '현재가', 'PER', 'PBR', 'ROE', '부채비율(%)', '영업이익(억)']
    if 'RSI' in display_df.columns: display_cols.append('RSI')
    st.dataframe(display_df[display_cols], use_container_width=True, hide_index=True)
    # [신규] 스크롤 방지를 위한 3개의 탭(Tab) 생성
    tab1, tab2, tab3 = st.tabs(["📊 1. 검색 결과 리스트", "🚦 2. 정밀 분석 대시보드", "🤖 3. AI 리포트 & 호가창"])

    csv = convert_df_to_csv(display_df[display_cols])
    st.download_button(label="📥 엑셀(CSV)로 리스트 다운로드", data=csv, file_name='quant_watchlist.csv', mime='text/csv')
    
    st.divider()
    st.header("🚦 가치평가 & 스마트머니 & 차트 대시보드")
    
    selected_names = st.multiselect("비교할 종목들을 선택하세요", final_df['Name'].tolist(), default=final_df['Name'].tolist()[:3])
    # --- 탭 1: 검색 결과 리스트 ---
    with tab1:
        st.subheader(f"✅ 조건검색 결과 ({len(final_df)}개 발견)")
        display_df = final_df.copy()
        display_df['시가총액(억)'] = display_df['시가총액(억)'].apply(lambda x: f"{x:,}")
        display_df['영업이익(억)'] = display_df['영업이익(억)'].apply(lambda x: f"{int(x):,}") 
        display_df['현재가'] = display_df['현재가'].apply(lambda x: f"{int(x):,}") 
        
        display_cols = ['Code', 'Name', '업종', '시가총액(억)', '현재가', 'PER', 'PBR', 'ROE', '부채비율(%)', '영업이익(억)']
        if 'RSI' in display_df.columns: display_cols.append('RSI')
        st.dataframe(display_df[display_cols], use_container_width=True, hide_index=True)
        
        csv = convert_df_to_csv(display_df[display_cols])
        st.download_button(label="📥 엑셀(CSV)로 리스트 다운로드", data=csv, file_name='quant_watchlist.csv', mime='text/csv')

    if st.button("🚀 선택 종목 정밀 비교", use_container_width=True) and selected_names:
        with st.spinner('차트 지표, 가치평가, 캔들/볼린저밴드 데이터를 융합 분석 중입니다...'):
            compare_results = []
            
            for name in selected_names:
                row = final_df[final_df['Name'] == name].iloc[0]
                code = row['Code']
                per, pbr, roe = row['PER'], row['PBR'], row['ROE']
                
                df_price = fdr.DataReader(code).tail(400)
                if df_price.empty: continue
                
                inst_sum, frgn_sum = check_smart_money(code)
                if inst_sum > 0 and frgn_sum > 0: money_sig = "🔥 양매수"
                elif inst_sum > 0: money_sig = "🟢 기관 매수"
                elif frgn_sum > 0: money_sig = "🟢 외인 매수"
                else: money_sig = "🔴 수급 이탈"

                df_price['MA20'] = df_price['Close'].rolling(window=20).mean()
                df_price['MA60'] = df_price['Close'].rolling(window=60).mean()
                df_price['MA120'] = df_price['Close'].rolling(window=120).mean()
                
                exp1 = df_price['Close'].ewm(span=12, adjust=False).mean()
                exp2 = df_price['Close'].ewm(span=26, adjust=False).mean()
                macd_series = exp1 - exp2
                signal_series = macd_series.ewm(span=9, adjust=False).mean()
                hist_series = macd_series - signal_series
    # --- 탭 2: 정밀 분석 대시보드 ---
    with tab2:
        st.info("💡 종목을 선택하여 수급, 볼린저밴드, 신용 위험도, 52주 신고가 모멘텀을 한눈에 비교하세요.")
        selected_names = st.multiselect("비교할 종목들을 선택하세요", final_df['Name'].tolist(), default=final_df['Name'].tolist()[:3])
        
        if st.button("🚀 선택 종목 정밀 비교", use_container_width=True) and selected_names:
            with st.spinner('차트 지표, 리스크, 모멘텀 데이터를 융합 분석 중입니다...'):
                compare_results = []
                backtest_results = []

                if len(df_price) >= 20:
                    avg_vol_20 = df_price['Volume'].rolling(window=20).mean().iloc[-2]
                    cur_vol = df_price['Volume'].iloc[-1]
                    vol_ratio = (cur_vol / avg_vol_20) * 100 if avg_vol_20 > 0 else 0
                    if vol_ratio >= 200: vol_sig = f"🔥 폭발 ({int(vol_ratio)}%)"
                    elif vol_ratio >= 120: vol_sig = f"🟢 증가 ({int(vol_ratio)}%)"
                    else: vol_sig = f"⚪ 평이 ({int(vol_ratio)}%)"
                for name in selected_names:
                    row = final_df[final_df['Name'] == name].iloc[0]
                    code = row['Code']
                    per, pbr, roe = row['PER'], row['PBR'], row['ROE']
                    credit_ratio = row.get('신용비율(%)', 0.0)

                    std20 = df_price['Close'].rolling(window=20).std().iloc[-1]
                    ma20_cur = df_price['MA20'].iloc[-1]
                    upper_band, lower_band = ma20_cur + (std20 * 2), ma20_cur - (std20 * 2)
                    cur_price_val = df_price['Close'].iloc[-1]
                    bandwidth = (upper_band - lower_band) / ma20_cur if ma20_cur > 0 else 0
                    # 1. 신용잔고 리스크 판독
                    if credit_ratio >= 8.0: credit_sig = f"💣 위험 ({credit_ratio}%)"
                    elif credit_ratio >= 4.0: credit_sig = f"⚠️ 주의 ({credit_ratio}%)"
                    else: credit_sig = f"🟢 안전 ({credit_ratio}%)"

                    if cur_price_val <= lower_band * 1.02: bb_sig = "🟢 하한선 터치"
                    elif cur_price_val >= upper_band * 0.98: bb_sig = "🔴 상한선 터치"
                    elif bandwidth < 0.10: bb_sig = "🔥 스퀴즈"
                    else: bb_sig = "⚪ 밴드 내 순항"
                else:
                    vol_sig, bb_sig = "-", "-"

                candle_sig = detect_candle_pattern(df_price)
                
                def get_historical_signals(idx):
                    if idx < -len(df_price): return "-", "-"
                    ma20, ma60, ma120 = df_price['MA20'].iloc[idx], df_price['MA60'].iloc[idx], df_price['MA120'].iloc[idx]
                    if pd.isna(ma120): trend = "알수없음"
                    elif ma20 > ma60 > ma120: trend = "🟢 정배열"
                    elif ma20 < ma60 < ma120: trend = "🔴 역배열"
                    else: trend = "🟡 혼조세"
                    df_price = fdr.DataReader(code).tail(400)
                    if df_price.empty: continue

                    m, s, h = macd_series.iloc[idx], signal_series.iloc[idx], hist_series.iloc[idx]
                    if pd.isna(m) or pd.isna(s): macd_sig = "알수없음"
                    elif m > s and h > 0: macd_sig = "🟢 골든크로스"
                    else: macd_sig = "🔴 데드크로스"
                    return trend, macd_sig

                current_price = df_price['Close'].iloc[-1]
                high_52w = df_price['High'].tail(250).max() 
                
                    inst_sum, frgn_sum = check_smart_money(code)
                    if inst_sum > 0 and frgn_sum > 0: money_sig = "🔥 양매수"
                    elif inst_sum > 0: money_sig = "🟢 기관 매수"
                    elif frgn_sum > 0: money_sig = "🟢 외인 매수"
                    else: money_sig = "🔴 수급 이탈"

                    df_price['MA20'] = df_price['Close'].rolling(window=20).mean()
                    df_price['MA60'] = df_price['Close'].rolling(window=60).mean()
                    df_price['MA120'] = df_price['Close'].rolling(window=120).mean()
                    
                    exp1 = df_price['Close'].ewm(span=12, adjust=False).mean()
                    exp2 = df_price['Close'].ewm(span=26, adjust=False).mean()
                    macd_series = exp1 - exp2
                    signal_series = macd_series.ewm(span=9, adjust=False).mean()
                    hist_series = macd_series - signal_series
                    
                    if len(df_price) >= 20:
                        avg_vol_20 = df_price['Volume'].rolling(window=20).mean().iloc[-2]
                        cur_vol = df_price['Volume'].iloc[-1]
                        vol_ratio = (cur_vol / avg_vol_20) * 100 if avg_vol_20 > 0 else 0
                        if vol_ratio >= 200: vol_sig = f"🔥 폭발 ({int(vol_ratio)}%)"
                        elif vol_ratio >= 120: vol_sig = f"🟢 증가 ({int(vol_ratio)}%)"
                        else: vol_sig = f"⚪ 평이 ({int(vol_ratio)}%)"
                        
                        std20 = df_price['Close'].rolling(window=20).std().iloc[-1]
                        ma20_cur = df_price['MA20'].iloc[-1]
                        upper_band, lower_band = ma20_cur + (std20 * 2), ma20_cur - (std20 * 2)
                        cur_price_val = df_price['Close'].iloc[-1]
                        bandwidth = (upper_band - lower_band) / ma20_cur if ma20_cur > 0 else 0
                        
                        if cur_price_val <= lower_band * 1.02: bb_sig = "🟢 하한선 터치"
                        elif cur_price_val >= upper_band * 0.98: bb_sig = "🔴 상한선 터치"
                        elif bandwidth < 0.10: bb_sig = "🔥 스퀴즈"
                        else: bb_sig = "⚪ 밴드 내 순항"
                    else:
                        vol_sig, bb_sig = "-", "-"

                    candle_sig = detect_candle_pattern(df_price)
                    
                    def get_historical_signals(idx):
                        if idx < -len(df_price): return "-", "-"
                        ma20, ma60, ma120 = df_price['MA20'].iloc[idx], df_price['MA60'].iloc[idx], df_price['MA120'].iloc[idx]
                        if pd.isna(ma120): trend = "알수없음"
                        elif ma20 > ma60 > ma120: trend = "🟢 정배열"
                        elif ma20 < ma60 < ma120: trend = "🔴 역배열"
                        else: trend = "🟡 혼조세"
                        m, s, h = macd_series.iloc[idx], signal_series.iloc[idx], hist_series.iloc[idx]
                        if pd.isna(m) or pd.isna(s): macd_sig = "알수없음"
                        elif m > s and h > 0: macd_sig = "🟢 골든크로스"
                        else: macd_sig = "🔴 데드크로스"
                        return trend, macd_sig

                    current_price = df_price['Close'].iloc[-1]
                    high_52w = df_price['High'].tail(250).max() 
                    
                    # 2. 52주 신고가 모멘텀 판독
                    breakout_ratio = (current_price / high_52w) * 100 if high_52w > 0 else 0
                    if breakout_ratio >= 98: momentum_sig = "🦅 신고가 돌파"
                    elif breakout_ratio >= 90: momentum_sig = "↗️ 돌파 시도"
                    else: momentum_sig = "⚪ 하단 횡보"
                    
                    cur_trend, cur_macd = get_historical_signals(-1)
                    drawdown = ((current_price - high_52w) / high_52w) * 100
                    dd_signal = f"🟢 {drawdown:.1f}%" if drawdown > -20 else f"🔴 {drawdown:.1f}%"
                    
                    compare_results.append({
                        '종목명': name, '현재가': f"{int(current_price):,}원",
                        '① 이평선': cur_trend, '② MACD': cur_macd, 
                        '③ 수급': money_sig, '④ 방어율(눌림)': dd_signal, 
                        '⑤ 거래량': vol_sig, '⑥ 캔들': candle_sig, '⑦ 볼린저': bb_sig,
                        '⑧ 신용(빚)': credit_sig, '⑨ 모멘텀': momentum_sig
                    })
                    
                    periods = [("3개월 전", -60), ("6개월 전", -120), ("1년 전", -250)]
                    for period_name, idx in periods:
                        if len(df_price) >= abs(idx):
                            price_past = df_price['Close'].iloc[idx]
                            price_past_str = f"{int(price_past):,}원"
                            ret = ((current_price - price_past) / price_past) * 100
                            ret_str = f"📈 +{ret:.1f}%" if ret > 0 else f"📉 {ret:.1f}%"
                            trend_past, macd_past = get_historical_signals(idx)
                        else:
                            price_past_str, ret_str, trend_past, macd_past = "-", "상장기간 부족", "-", "-"
                            
                        backtest_results.append({
                            '종목명': name, '투자 시점': period_name, '당시 주가': price_past_str,
                            '현재 수익률': ret_str, '당시 이평선': trend_past, '당시 MACD': macd_past
                        })
                    
                if compare_results:
                    st.dataframe(pd.DataFrame(compare_results), use_container_width=True, hide_index=True)
                    st.markdown("---")
                    st.subheader("⏪ 미니 백테스팅 (과거 매수 시점의 주가와 수익률)")
                    st.dataframe(pd.DataFrame(backtest_results), use_container_width=True, hide_index=True)

    # --- 탭 3: AI 리포트 및 실시간 호가창 ---
    with tab3:
        target_name = st.selectbox("리포트를 생성할 최종 타겟 종목 1개를 선택하세요", final_df['Name'].tolist())
        target_code = final_df[final_df['Name'] == target_name]['Code'].values[0]
        
        col1, col2 = st.columns(2)
        with col1:
            report_btn = st.button(f"📝 {target_name} AI 리포트 생성", use_container_width=True)
        with col2:
            naver_url = f"https://stock.naver.com/domestic/stock/{target_code}/price"
            st.link_button(f"🔴 {target_name} 실시간 호가창 보기 (새 창)", naver_url, use_container_width=True)
        
        if report_btn:
            with st.status("AI 리포트 작성 중... (거시경제, 리스크, 52주 모멘텀 분석 포함)", expanded=True) as status:
try:
                    bps = current_price / pbr if pbr > 0 else 0
                    s_rim_price = bps * (roe / 8) if roe > 0 else 0 
                    eps = current_price / per if per > 0 else 0
                    g = min(roe, 15) if roe > 0 else 0
                    graham_price = eps * (8.5 + 2 * g)
                except: s_rim_price, graham_price = 0, 0
                
                cur_trend, cur_macd = get_historical_signals(-1)
                drawdown = ((current_price - high_52w) / high_52w) * 100
                dd_signal = f"🟢 {drawdown:.1f}%" if drawdown > -20 else f"🔴 {drawdown:.1f}%"
                
                compare_results.append({
                    '종목명': name, '현재가': f"{int(current_price):,}원",
                    'S-RIM 적정가': f"{int(s_rim_price):,}원" if s_rim_price else "-",
                    '① 이평선': cur_trend, '② MACD': cur_macd, 
                    '③ 스마트머니': money_sig, '④ 방어율': dd_signal, 
                    '⑤ 거래량': vol_sig, '⑥ 캔들': candle_sig, '⑦ 볼린저': bb_sig
                })
                
            if compare_results:
                st.dataframe(pd.DataFrame(compare_results), use_container_width=True, hide_index=True)
                    row = final_df[final_df['Name'] == target_name].iloc[0]
                    df_target = fdr.DataReader(target_code).tail(120)
                    cur_price = df_target['Close'].iloc[-1]
                    cur_vol = df_target['Volume'].iloc[-1]
                    
                    avg_vol_20 = df_target['Volume'].rolling(window=20).mean().iloc[-2] if len(df_target) >= 20 else 0
                    vol_ratio = (cur_vol / avg_vol_20) * 100 if avg_vol_20 > 0 else 0
                    high_52w = df_target['High'].max()
                    
                    ma20 = df_target['Close'].rolling(window=20).mean().iloc[-1]
                    ma60 = df_target['Close'].rolling(window=60).mean().iloc[-1]
                    ma120 = df_target['Close'].rolling(window=120).mean().iloc[-1]
                    trend_state = "정배열(상승추세)" if ma20 > ma60 > ma120 else ("역배열(하락추세)" if ma20 < ma60 < ma120 else "혼조세")
                    
                    macd, signal_line, hist = calculate_macd(df_target)
                    macd_state = "골든크로스(매수우위)" if macd > signal_line and hist > 0 else "데드크로스(매도우위)"
                    rsi_val = calculate_rsi(df_target).iloc[-1]
                    candle_state = detect_candle_pattern(df_target)

                    if len(df_target) >= 20:
                        std20 = df_target['Close'].rolling(window=20).std().iloc[-1]
                        upper_band, lower_band = ma20 + (std20 * 2), ma20 - (std20 * 2)
                        bandwidth = (upper_band - lower_band) / ma20 if ma20 > 0 else 0
                        if cur_price <= lower_band * 1.02: bb_state = "하한선 터치 (통계적 과매도, 반등 지지선 부근)"
                        elif cur_price >= upper_band * 0.98: bb_state = "상한선 터치 (통계적 과매수, 저항선 부근)"
                        elif bandwidth < 0.10: bb_state = "밴드 수축/스퀴즈 (에너지 응축 중, 큰 변동성 예상)"
                        else: bb_state = "밴드 중심부 순항 중"
                    else: bb_state = "데이터 부족"
                    
                    breakout_ratio = (cur_price / high_52w) * 100 if high_52w > 0 else 0
                    if breakout_ratio >= 98: momentum_state = "52주 신고가 돌파 (강한 상승 모멘텀)"
                    elif breakout_ratio >= 90: momentum_state = "52주 신고가 근접 (돌파 시도 중)"
                    else: momentum_state = "박스권 하단 혹은 하락 추세 중"

    st.divider()
    st.subheader("🤖 1:1 AI 심층 리포트 & 실시간 호가창 대응")
    
    target_name = st.selectbox("리포트를 생성할 최종 타겟 종목 1개를 선택하세요", final_df['Name'].tolist())
    target_code = final_df[final_df['Name'] == target_name]['Code'].values[0]
    
    col1, col2 = st.columns(2)
    with col1:
        report_btn = st.button(f"📝 {target_name} AI 리포트 생성", use_container_width=True)
    with col2:
        # [수정 완료] 질문자님이 찾아주신 완벽한 실시간 호가창 주소 적용!
        naver_url = f"https://stock.naver.com/domestic/stock/{target_code}/price"
        st.link_button(f"🔴 {target_name} 실시간 호가창 보기 (새 창)", naver_url, use_container_width=True)
    
    if report_btn:
        with st.status("AI 리포트 작성 중... (거시경제, 차트 판독, 수급 데이터 수집 포함)", expanded=True) as status:
            # ... (이 아래 AI 리포트 생성 로직은 기존과 100% 동일하게 두시면 됩니다!) ...
            try:
                row = final_df[final_df['Name'] == target_name].iloc[0]
                df_target = fdr.DataReader(target_code).tail(120)
                cur_price = df_target['Close'].iloc[-1]
                cur_vol = df_target['Volume'].iloc[-1]
                
                avg_vol_20 = df_target['Volume'].rolling(window=20).mean().iloc[-2] if len(df_target) >= 20 else 0
                vol_ratio = (cur_vol / avg_vol_20) * 100 if avg_vol_20 > 0 else 0
                high_52w = df_target['High'].max()
                
                ma20 = df_target['Close'].rolling(window=20).mean().iloc[-1]
                ma60 = df_target['Close'].rolling(window=60).mean().iloc[-1]
                ma120 = df_target['Close'].rolling(window=120).mean().iloc[-1]
                trend_state = "정배열(상승추세)" if ma20 > ma60 > ma120 else ("역배열(하락추세)" if ma20 < ma60 < ma120 else "혼조세")
                
                macd, signal_line, hist = calculate_macd(df_target)
                macd_state = "골든크로스(매수우위)" if macd > signal_line and hist > 0 else "데드크로스(매도우위)"
                rsi_val = calculate_rsi(df_target).iloc[-1]
                candle_state = detect_candle_pattern(df_target)

                if len(df_target) >= 20:
                    std20 = df_target['Close'].rolling(window=20).std().iloc[-1]
                    upper_band, lower_band = ma20 + (std20 * 2), ma20 - (std20 * 2)
                    bandwidth = (upper_band - lower_band) / ma20 if ma20 > 0 else 0
                    if cur_price <= lower_band * 1.02: bb_state = "하한선 터치 (통계적 과매도, 반등 지지선 부근)"
                    elif cur_price >= upper_band * 0.98: bb_state = "상한선 터치 (통계적 과매수, 저항선 부근)"
                    elif bandwidth < 0.10: bb_state = "밴드 수축/스퀴즈 (에너지 응축 중, 큰 변동성 예상)"
                    else: bb_state = "밴드 중심부 순항 중"
                else: bb_state = "데이터 부족"

                df_kospi = fdr.DataReader('KS11').tail(20)
                kospi_state = "강세장" if df_kospi['Close'].iloc[-1] > df_kospi['Close'].mean() else "약세장"
                
                try: df_usd = fdr.DataReader('USD/KRW').tail(1); usd_krw = df_usd['Close'].iloc[0]
                except: usd_krw = "데이터 없음"
                    df_kospi = fdr.DataReader('KS11').tail(20)
                    kospi_state = "강세장" if df_kospi['Close'].iloc[-1] > df_kospi['Close'].mean() else "약세장"
                    
                    try: df_usd = fdr.DataReader('USD/KRW').tail(1); usd_krw = df_usd['Close'].iloc[0]
                    except: usd_krw = "데이터 없음"

                dividend = row.get('배당(%)', 0.0)
                
                report_data = f"""
                [종목 정보] {target_name} (코드: {target_code})
                [재무/가치] PER: {row['PER']}배, PBR: {row['PBR']}배, ROE: {row['ROE']}%, 부채비율: {row['부채비율(%)']}%, 시가총액: {row['시가총액(억)']}억원, 최근 영업이익: {row['영업이익(억)']}억원
                [기술 분석] 현재가: {cur_price:,}원, 52주 최고가: {high_52w:,}원, 이평선 추세: {trend_state}, MACD: {macd_state}, RSI(14): {rsi_val:.1f}
                [볼린저 밴드] 현재 위치: {bb_state}
                [캔들 패턴] 오늘의 캔들: {candle_state}
                [거래량 변동] 금일 거래량: {cur_vol:,}주, 20일 평균 거래량 대비 {vol_ratio:.1f}% 수준
                [거버넌스] 시가배당률: {dividend}%
                [매크로] 코스피 시장 상태: {kospi_state}, 원/달러 환율: {usd_krw}원
                """
                
                prompt = f"""
                당신은 프랍 트레이딩 펌의 수석 수석 애널리스트입니다. 다음 제공된 데이터를 바탕으로 14개 항목 투자 리포트를 한국어로 작성하라. 
                제공된 데이터가 있다면 막연한 소리 대신 해당 숫자를 반드시 인용하여 분석할 것.
                제공된 데이터: {report_data}
                항목: 1.요약 2.개요 3.재무분석 4.밸류에이션 5.산업/경쟁 6.기술분석(이평선, MACD, 거래량 비율, 볼린저 밴드, 캔들 패턴의 의미를 종합적으로 반드시 포함) 7.거버넌스 8.매크로 9.촉매 10.베어케이스 11.시나리오 12.점수산출 13.최종판단 14.출처(네이버 금융 명시)
                """
                
                response = model.generate_content(prompt)
                status.update(label="분석 완료!", state="complete", expanded=False)
                st.markdown(response.text)
            except Exception as e:
                st.error(f"리포트 생성 중 에러 발생: {e}")
                    dividend = row.get('배당(%)', 0.0)
                    credit_ratio = row.get('신용비율(%)', 0.0)
                    
                    report_data = f"""
                    [종목 정보] {target_name} (코드: {target_code})
                    [재무/가치] PER: {row['PER']}배, PBR: {row['PBR']}배, ROE: {row['ROE']}%, 부채비율: {row['부채비율(%)']}%, 시가총액: {row['시가총액(억)']}억원, 최근 영업이익: {row['영업이익(억)']}억원
                    [기술 분석] 현재가: {cur_price:,}원, 52주 최고가: {high_52w:,}원, 이평선 추세: {trend_state}, MACD: {macd_state}, RSI(14): {rsi_val:.1f}
                    [볼린저 밴드] 현재 위치: {bb_state}
                    [캔들 패턴] 오늘의 캔들: {candle_state}
                    [거래량 변동] 금일 거래량: {cur_vol:,}주, 20일 평균 거래량 대비 {vol_ratio:.1f}% 수준
                    [모멘텀/리스크] 52주 신고가 상태: {momentum_state}, 신용잔고율: {credit_ratio}% (8% 이상시 악성 매물대 위험)
                    [거버넌스] 시가배당률: {dividend}%
                    [매크로] 코스피 시장 상태: {kospi_state}, 원/달러 환율: {usd_krw}원
                    """
                    
                    prompt = f"""
                    당신은 프랍 트레이딩 펌의 수석 애널리스트입니다. 제공된 데이터를 바탕으로 14개 항목 투자 리포트를 작성하라. 
                    제공된 데이터가 있다면 막연한 소리 대신 해당 숫자를 반드시 인용하여 분석할 것.
                    제공된 데이터: {report_data}
                    항목: 1.요약 2.개요 3.재무분석 4.밸류에이션 5.산업/경쟁 6.기술분석(이평선, 거래량, 볼린저밴드, 캔들, 52주 모멘텀 의미 반드시 포함) 7.거버넌스 8.매크로 9.리스크(신용잔고율 반드시 언급) 10.베어케이스 11.시나리오 12.점수산출 13.최종판단 14.출처(네이버 금융 명시)
                    """
                    
                    response = model.generate_content(prompt)
                    status.update(label="분석 완료!", state="complete", expanded=False)
                    st.markdown(response.text)
                except Exception as e:
                    st.error(f"리포트 생성 중 에러 발생: {e}")
