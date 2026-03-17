import streamlit as st
import FinanceDataReader as fdr
import pandas as pd
import numpy as np
import requests
from bs4 import BeautifulSoup
import re
import google.generativeai as genai
import io

# --- [초기 설정 및 API 키 보안 세팅] ---
st.set_page_config(layout="wide", page_title="AI 퀀트 스캐너 & 애널리스트")

# 무조건 import config를 하지 않고, 여기서만 조심스럽게 시도합니다.
try:
    import config
    API_KEY = config.GEMINI_API_KEY
except ImportError:
    # 깃허브/클라우드 환경이라 config 파일이 없으면 비밀 금고에서 꺼냅니다.
    API_KEY = st.secrets["GEMINI_API_KEY"]

genai.configure(api_key=API_KEY)
model = genai.GenerativeModel('gemini-2.5-flash')

# (이 아래부터는 기존 코드와 완전히 동일합니다!)
# ...

# 한국거래소 전 종목 데이터 캐싱 (해외 IP 차단 우회 로직 포함 완전판)
@st.cache_data(ttl=3600)
def get_krx_data():
    try:
        df_krx = fdr.StockListing('KRX')
        if 'Sector' not in df_krx.columns:
            try:
                df_desc = fdr.StockListing('KRX-DESC')
                if 'Symbol' in df_desc.columns: df_desc = df_desc.rename(columns={'Symbol': 'Code'})
                df_krx = pd.merge(df_krx, df_desc[['Code', 'Sector']], on='Code', how='left')
            except:
                df_krx['Sector'] = '미분류'
        df_krx['Sector'] = df_krx['Sector'].fillna('미분류')
        return df_krx
    
    except Exception as e:
        data = []
        for sosok in [0, 1]: 
            # 최대 1000개 스캔을 대비해 네이버 우회 크롤링 범위를 25페이지(약 1200개씩)로 확장
            for page in range(1, 25): 
                url = f"https://finance.naver.com/sise/sise_market_sum.naver?sosok={sosok}&page={page}"
                res = requests.get(url, headers={'User-agent': 'Mozilla/5.0'})
                soup = BeautifulSoup(res.text, 'html.parser')
                table = soup.find('table', {'class': 'type_2'})
                
                if not table: continue
                
                for row in table.find_all('tr'):
                    cols = row.find_all('td')
                    if len(cols) >= 7:
                        a_tag = cols[1].find('a')
                        if a_tag:
                            code = a_tag['href'].split('code=')[-1]
                            name = a_tag.text.strip()
                            price_str = cols[2].text.strip().replace(',', '')
                            marcap_str = cols[6].text.strip().replace(',', '')
                            
                            try:
                                marcap = int(marcap_str) * 100000000 
                                price = int(price_str)
                                stocks = int(marcap / price) if price > 0 else 1
                            except:
                                marcap, stocks, price = 0, 1, 0
                                
                            data.append({
                                'Code': code, 'Name': name, 'Sector': '미분류', 
                                'Marcap': marcap, 'Stocks': stocks, 'Price': price
                            })
        
        df_backup = pd.DataFrame(data)
        df_backup = df_backup.sort_values('Marcap', ascending=False).reset_index(drop=True)
        return df_backup

def calculate_rsi(df, period=14):
    if len(df) < period: return 50.0
    delta = df['Close'].diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.ewm(com=period-1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period-1, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def calculate_macd(df, short=12, long=26, signal=9):
    if len(df) < long: return 0, 0, 0
    exp1 = df['Close'].ewm(span=short, adjust=False).mean()
    exp2 = df['Close'].ewm(span=long, adjust=False).mean()
    macd = exp1 - exp2
    signal_line = macd.ewm(span=signal, adjust=False).mean()
    macd_hist = macd - signal_line
    return macd.iloc[-1], signal_line.iloc[-1], macd_hist.iloc[-1]

def safe_float(text):
    try:
        if not text or text.strip() in ['-', 'N/A', '']: return 0.0
        return float(text.strip().replace(',', ''))
    except:
        return 0.0

def get_recent_fin_value(soup, keyword):
    try:
        for th in soup.find_all('th'):
            if keyword in th.text:
                row = th.parent
                tds = row.find_all('td')
                for td in reversed(tds):
                    val = td.text.strip().replace(',', '')
                    if val and val not in ['-', 'N/A']:
                        return float(val)
                break
    except Exception:
        pass
    return 0.0

def check_smart_money(code):
    try:
        url = f"https://finance.naver.com/item/frgn.naver?code={code}"
        res = requests.get(url, headers={'User-agent': 'Mozilla/5.0'})
        dfs = pd.read_html(io.StringIO(res.text), match='날짜')
        df_frgn = dfs[0].dropna(subset=['날짜'])
        recent_5 = df_frgn.head(5)
        inst_sum = pd.to_numeric(recent_5.iloc[:, 5].astype(str).str.replace(',', ''), errors='coerce').sum()
        frgn_sum = pd.to_numeric(recent_5.iloc[:, 6].astype(str).str.replace(',', ''), errors='coerce').sum()
        return inst_sum, frgn_sum
    except Exception:
        return 0, 0

def convert_df_to_csv(df):
    return df.to_csv(index=False, encoding='utf-8-sig')

# --- [단기 기억 장치 초기화] ---
if 'min_marcap' not in st.session_state: st.session_state.min_marcap = 5000
if 'target_per' not in st.session_state: st.session_state.target_per = 15
if 'min_roe' not in st.session_state: st.session_state.min_roe = 10
if 'target_pbr' not in st.session_state: st.session_state.target_pbr = 1.5
if 'max_debt' not in st.session_state: st.session_state.max_debt = 150
if 'target_rsi' not in st.session_state: st.session_state.target_rsi = 70
if 'min_price' not in st.session_state: st.session_state.min_price = 2000 # [신규] 기본 최소 주가 2,000원
if 'scanned_data' not in st.session_state: st.session_state.scanned_data = None 

if 'use_marcap' not in st.session_state: st.session_state.use_marcap = True
if 'use_per' not in st.session_state: st.session_state.use_per = True
if 'use_pbr' not in st.session_state: st.session_state.use_pbr = True
if 'use_roe' not in st.session_state: st.session_state.use_roe = True
if 'use_debt' not in st.session_state: st.session_state.use_debt = True
if 'use_op' not in st.session_state: st.session_state.use_op = True 
if 'use_rsi' not in st.session_state: st.session_state.use_rsi = True
if 'use_min_price' not in st.session_state: st.session_state.use_min_price = True # [신규] 동전주 방어 켜기

# --- [사이드바: 종목 선별 기준] ---
st.sidebar.header("🔍 1단계: 펀더멘털 필터 (재무)")

with st.sidebar.expander("❓ 용어 및 적정주가 모델 설명"):
    st.caption("✅ **PER**: 주가/주당순이익. 낮을수록 저평가.")
    st.caption("✅ **PBR**: 주가/주당순자산. 1배 미만은 장부가치보다 저렴.")
    st.caption("✅ **ROE**: 자기자본이익률. 진정한 수익 창출 능력.")
    st.divider()
    st.caption("🎯 **S-RIM**: 사경인 회계사의 초과이익 모델 (한국형)")
    st.caption("🎯 **그레이엄**: 벤저민 그레이엄의 내재가치 공식")
    st.caption("📈 **MACD**: 이동평균선 기반 추세 전환 지표")
    st.caption("💸 **스마트머니**: 최근 5거래일 외국인/기관 순매수 합산")

# [신규] 1000개 옵션 추가
scan_limit = st.sidebar.selectbox("검사할 종목 수 (시총 상위)", [50, 100, 200, 500, 1000], index=1, key="main_scan_limit", help="국내 상장사 중 시가총액이 높은 순서대로 훑을 개수를 결정합니다. 1,000개 선택 시 네이버 서버 크롤링에 3~5분 정도 소요될 수 있습니다.")

df_krx_full = get_krx_data()
sectors_list = [s for s in df_krx_full['Sector'].unique() if isinstance(s, str)]
sectors_list.sort()
default_excludes = [s for s in ['금융업', '보험업'] if s in sectors_list]

excluded_sectors = st.sidebar.multiselect(
    "🚫 제외할 업종 (가치 트랩 방어)", 
    options=sectors_list, 
    default=default_excludes,
    help="만년 저평가인 금융주나 지주사 등을 검색에서 원천 제외합니다."
)

st.session_state.use_marcap = st.sidebar.checkbox("✅ 최소 시가총액 적용", value=st.session_state.use_marcap)
st.session_state.min_marcap = st.sidebar.number_input("시가총액 (억원)", value=st.session_state.min_marcap, step=100, disabled=not st.session_state.use_marcap)
st.session_state.min_marcap = st.sidebar.slider("드래그 조절", 0, 100000, st.session_state.min_marcap, step=100, label_visibility="collapsed", disabled=not st.session_state.use_marcap)

# [신규 추가] 동전주(잡주) 제외 필터
st.sidebar.divider()
st.sidebar.header("🛡️ 1.5단계: 동전주 방어막 (가격)")
st.session_state.use_min_price = st.sidebar.checkbox("✅ 최소 주가 적용 (동전주 제외)", value=st.session_state.use_min_price)
st.session_state.min_price = st.sidebar.number_input("최소 주가 (원)", value=st.session_state.min_price, step=500, disabled=not st.session_state.use_min_price, help="세력의 장난이 심하거나 상장폐지 리스크가 있는 1,000원~2,000원 미만의 동전주를 검색에서 원천 차단합니다.")

st.sidebar.divider()
st.sidebar.header("🔍 2단계: 세부 재무 비율")

st.session_state.use_per = st.sidebar.checkbox("✅ 최대 PER 적용", value=st.session_state.use_per)
st.session_state.target_per = st.sidebar.number_input("PER (배)", value=st.session_state.target_per, step=1, disabled=not st.session_state.use_per)
st.session_state.target_per = st.sidebar.slider("드래그 조절 ", 1, 100, st.session_state.target_per, label_visibility="collapsed", disabled=not st.session_state.use_per)

st.session_state.use_pbr = st.sidebar.checkbox("✅ 최대 PBR 적용", value=st.session_state.use_pbr)
st.session_state.target_pbr = st.sidebar.number_input("PBR (배)", value=st.session_state.target_pbr, step=0.1, disabled=not st.session_state.use_pbr)
st.session_state.target_pbr = st.sidebar.slider("드래그 조절  ", 0.1, 10.0, st.session_state.target_pbr, step=0.1, label_visibility="collapsed", disabled=not st.session_state.use_pbr)

st.session_state.use_roe = st.sidebar.checkbox("✅ 최소 ROE 적용", value=st.session_state.use_roe)
st.session_state.min_roe = st.sidebar.number_input("ROE (%)", value=st.session_state.min_roe, step=1, disabled=not st.session_state.use_roe)
st.session_state.min_roe = st.sidebar.slider("드래그 조절   ", 0, 50, st.session_state.min_roe, label_visibility="collapsed", disabled=not st.session_state.use_roe)

st.session_state.use_debt = st.sidebar.checkbox("✅ 최대 부채비율 적용", value=st.session_state.use_debt)
st.session_state.max_debt = st.sidebar.number_input("부채비율 (%)", value=st.session_state.max_debt, step=10, disabled=not st.session_state.use_debt)
st.session_state.max_debt = st.sidebar.slider("드래그 조절    ", 10, 500, st.session_state.max_debt, step=10, label_visibility="collapsed", disabled=not st.session_state.use_debt)

st.session_state.use_op = st.sidebar.checkbox("✅ 영업이익 흑자(+) 유지", value=st.session_state.use_op)

st.sidebar.divider()
st.sidebar.header("📈 3단계: 기술적 타이밍")

st.session_state.use_rsi = st.sidebar.checkbox("✅ 최대 RSI 적용", value=st.session_state.use_rsi)
st.session_state.target_rsi = st.sidebar.number_input("RSI (14일)", value=st.session_state.target_rsi, step=1, disabled=not st.session_state.use_rsi)
st.session_state.target_rsi = st.sidebar.slider("드래그 조절     ", 10, 100, st.session_state.target_rsi, step=1, label_visibility="collapsed", disabled=not st.session_state.use_rsi)

st.sidebar.divider()
scan_button = st.sidebar.button("🎯 스캐너 가동 (클릭!)", use_container_width=True)

# --- [메인 화면 로직: 매크로 풍향계 & 스캔] ---
st.title("📈 AI 주식 발굴 및 심층 분석 시스템")

try:
    df_kospi = fdr.DataReader('KS11').tail(20)
    cur_kospi = df_kospi['Close'].iloc[-1]
    ma20_kospi = df_kospi['Close'].mean()
    if cur_kospi >= ma20_kospi:
        st.success(f"🧭 **오늘의 시장 풍향계:** 🟢 강세장 (코스피 20일선 돌파 유지 중) | 현재가: {cur_kospi:,.2f}pt\n\n💡 **AI 전략 조언:** 시장에 돈이 돌고 있습니다. RSI 70 이하의 정배열 우량주를 적극적으로 공략하세요!")
    else:
        st.error(f"🧭 **오늘의 시장 풍향계:** 🔴 약세장 (코스피 20일선 이탈) | 현재가: {cur_kospi:,.2f}pt\n\n💡 **AI 전략 조언:** 시장이 조정을 받고 있습니다. 가치평가(S-RIM) 대비 매우 저렴하고 RSI가 30 근처인 종목만 보수적으로 접근하세요.")
except:
    pass

if scan_button:
    with st.spinner(f'1차: 시총 상위 {scan_limit}개 종목 로드 중... (1000개 선택 시 최대 5분 소요)'):
        df_krx = get_krx_data()
        
        if excluded_sectors:
            df_krx = df_krx[~df_krx['Sector'].isin(excluded_sectors)]
            
        if st.session_state.use_marcap:
            min_marcap_won = st.session_state.min_marcap * 100000000
            filtered_by_cap = df_krx[df_krx['Marcap'] >= min_marcap_won].sort_values('Marcap', ascending=False).head(scan_limit)
        else:
            filtered_by_cap = df_krx.sort_values('Marcap', ascending=False).head(scan_limit)

    if not filtered_by_cap.empty:
        progress_text = f"2차: 네이버 금융 재무 데이터 정밀 추출 중..."
        progress_bar = st.progress(0, text=progress_text)
        fin_results = []
        total_stocks = len(filtered_by_cap)
        
        for idx, row in enumerate(filtered_by_cap.itertuples()):
            code = row.Code
            url = f"https://finance.naver.com/item/main.naver?code={code}"
            try:
                res = requests.get(url, headers={'User-agent': 'Mozilla/5.0'})
                soup = BeautifulSoup(res.text, 'html.parser')
                
                per = safe_float(soup.select_one('#_per').text if soup.select_one('#_per') else "0")
                pbr = safe_float(soup.select_one('#_pbr').text if soup.select_one('#_pbr') else "0")
                dvr = safe_float(soup.select_one('#_dvr').text if soup.select_one('#_dvr') else "0")
                roe = (pbr / per) * 100 if per > 0 else 0.0
                debt_ratio = get_recent_fin_value(soup, '부채비율')
                op_profit = get_recent_fin_value(soup, '영업이익')
                
                # 우회 크롤링으로 들어왔을 때는 row.Price가 없을 수 있으므로 안전하게 처리
                current_price = getattr(row, 'Price', int(row.Marcap / float(row.Stocks)) if getattr(row, 'Stocks', 0) else 0)
                
                fin_results.append({
                    'Code': code, 'Name': row.Name, '업종': row.Sector, '시가총액(억)': int(row.Marcap // 100000000), '현재가': current_price,
                    'PER': round(per, 2), 'PBR': round(pbr, 2), 'ROE': round(roe, 2),
                    '부채비율(%)': round(debt_ratio, 2), '영업이익(억)': op_profit, '배당(%)': dvr
                })
            except: pass 
            progress_bar.progress((idx + 1) / total_stocks, text=f"{progress_text} ({idx+1}/{total_stocks} 완료)")
        
        progress_bar.empty()
        df_fin = pd.DataFrame(fin_results)
        
        if not df_fin.empty:
            mask = pd.Series(True, index=df_fin.index)
            if st.session_state.use_per: mask = mask & (df_fin['PER'] > 0) & (df_fin['PER'] <= st.session_state.target_per)
            if st.session_state.use_pbr: mask = mask & (df_fin['PBR'] > 0) & (df_fin['PBR'] <= st.session_state.target_pbr)
            if st.session_state.use_roe: mask = mask & (df_fin['ROE'] >= st.session_state.min_roe)
            if st.session_state.use_debt: mask = mask & (df_fin['부채비율(%)'] <= st.session_state.max_debt)
            if st.session_state.use_op: mask = mask & (df_fin['영업이익(억)'] > 0)
            # [신규 추가] 동전주(최소 주가) 방어막 마스크 적용
            if st.session_state.use_min_price: mask = mask & (df_fin['현재가'] >= st.session_state.min_price)
            
            survivors_df = df_fin[mask].copy()
        else:
            survivors_df = pd.DataFrame()

        if not survivors_df.empty:
            if not st.session_state.use_rsi:
                st.session_state.scanned_data = survivors_df.sort_values(by='ROE', ascending=False)
            else:
                progress_text2 = f"3차: 차트 분석(RSI) 진행 중..."
                progress_bar2 = st.progress(0, text=progress_text2)
                final_results = []
                total_survivors = len(survivors_df)
                survivors_records = survivors_df.to_dict('records')
                
                for idx, row_dict in enumerate(survivors_records):
                    try:
                        df_price = fdr.DataReader(row_dict['Code']).tail(40) 
                        if not df_price.empty:
                            rsi_val = calculate_rsi(df_price).iloc[-1]
                            if rsi_val <= st.session_state.target_rsi:
                                row_dict['RSI'] = round(rsi_val, 1)
                                final_results.append(row_dict)
                    except: pass
                    progress_bar2.progress((idx + 1) / total_survivors, text=f"{progress_text2} ({idx+1}/{total_survivors})")
                
                progress_bar2.empty()
                if final_results: st.session_state.scanned_data = pd.DataFrame(final_results).sort_values(by='ROE', ascending=False)
                else: st.session_state.scanned_data = pd.DataFrame()

# --- [메인 화면 출력: 워치리스트 및 대시보드] ---
if st.session_state.scanned_data is not None and not st.session_state.scanned_data.empty:
    final_df = st.session_state.scanned_data
    
    st.subheader(f"✅ 조건검색 결과 ({len(final_df)}개 발견)")
    display_df = final_df.copy()
    display_df['시가총액(억)'] = display_df['시가총액(억)'].apply(lambda x: f"{x:,}")
    display_df['영업이익(억)'] = display_df['영업이익(억)'].apply(lambda x: f"{int(x):,}") 
    display_df['현재가'] = display_df['현재가'].apply(lambda x: f"{int(x):,}") 
    
    display_cols = ['Code', 'Name', '업종', '시가총액(억)', '현재가', 'PER', 'PBR', 'ROE', '부채비율(%)', '영업이익(억)']
    if st.session_state.use_rsi and 'RSI' in display_df.columns: display_cols.append('RSI')
    st.dataframe(display_df[display_cols], use_container_width=True, hide_index=True)
    
    csv = convert_df_to_csv(display_df[display_cols])
    st.download_button(label="📥 엑셀(CSV)로 리스트 다운로드", data=csv, file_name='quant_watchlist.csv', mime='text/csv')
    
    st.divider()
    st.header("🚦 가치평가 & 스마트머니 & 백테스팅 대시보드")
    st.info("💡 장바구니에 종목을 담아 기관/외국인의 수급, 거래량 폭발 여부, 과거 백테스트 결과를 확인하세요.")
    
    selected_names = st.multiselect("비교할 종목들을 선택하세요 (여러 개 선택 가능)", final_df['Name'].tolist())
    
    if st.button("🚀 선택 종목 정밀 비교 & 백테스트", use_container_width=True) and selected_names:
        with st.spinner('차트 지표, 가치평가, 수급 및 거래량 데이터를 분석 중입니다...'):
            compare_results = []
            backtest_results = []
            
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
                
                if len(df_price) >= 20:
                    avg_vol_20 = df_price['Volume'].rolling(window=20).mean().iloc[-2]
                    cur_vol = df_price['Volume'].iloc[-1]
                    vol_ratio = (cur_vol / avg_vol_20) * 100 if avg_vol_20 > 0 else 0
                    if vol_ratio >= 200: vol_sig = f"🔥 폭발 ({int(vol_ratio)}%)"
                    elif vol_ratio >= 120: vol_sig = f"🟢 증가 ({int(vol_ratio)}%)"
                    else: vol_sig = f"⚪ 평이 ({int(vol_ratio)}%)"
                else:
                    vol_sig = "-"
                
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
                
                try:
                    bps = current_price / pbr if pbr > 0 else 0
                    s_rim_price = bps * (roe / 8) if roe > 0 else 0 
                    eps = current_price / per if per > 0 else 0
                    g = min(roe, 15) if roe > 0 else 0
                    graham_price = eps * (8.5 + 2 * g)
                except:
                    s_rim_price, graham_price = 0, 0
                
                cur_trend, cur_macd = get_historical_signals(-1)
                drawdown = ((current_price - high_52w) / high_52w) * 100
                dd_signal = f"🟢 {drawdown:.1f}%" if drawdown > -20 else f"🔴 {drawdown:.1f}%"
                
                compare_results.append({
                    '종목명': name, '현재가': f"{int(current_price):,}원",
                    'S-RIM 적정가': f"{int(s_rim_price):,}원" if s_rim_price else "-",
                    '그레이엄 가치': f"{int(graham_price):,}원" if graham_price else "-",
                    '① 이평선': cur_trend, '② MACD': cur_macd, 
                    '③ 스마트머니': money_sig, '④ 하락방어율': dd_signal, '⑤ 거래량(20일비)': vol_sig
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
                st.subheader("1. 가치평가 및 현재 수급/거래량 대시보드")
                st.dataframe(pd.DataFrame(compare_results), use_container_width=True, hide_index=True)
                
                st.subheader("2. 미니 백테스팅 (과거 매수 시점의 주가와 수익률)")
                st.dataframe(pd.DataFrame(backtest_results), use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("🤖 최종 승자를 가려라! 1:1 AI 심층 리포트")
    target_name = st.selectbox("리포트를 생성할 최종 타겟 종목 1개를 선택하세요", final_df['Name'].tolist())
    target_code = final_df[final_df['Name'] == target_name]['Code'].values[0]
    
    if st.button(f"📝 {target_name} AI 리포트 생성"):
        with st.status("AI 리포트 작성 중... (거시경제, 거래량 변동, 차트 데이터 수집 포함)", expanded=True) as status:
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

                df_kospi = fdr.DataReader('KS11').tail(20)
                kospi_state = "강세장" if df_kospi['Close'].iloc[-1] > df_kospi['Close'].mean() else "약세장"
                
                try:
                    df_usd = fdr.DataReader('USD/KRW').tail(1)
                    usd_krw = df_usd['Close'].iloc[0]
                except:
                    usd_krw = "데이터 없음"

                dividend = row.get('배당(%)', 0.0)
                
                report_data = f"""
                [종목 정보] {target_name} (코드: {target_code})
                [재무/가치] PER: {row['PER']}배, PBR: {row['PBR']}배, ROE: {row['ROE']}%, 부채비율: {row['부채비율(%)']}%, 시가총액: {row['시가총액(억)']}억원, 최근 영업이익: {row['영업이익(억)']}억원
                [기술 분석] 현재가: {cur_price:,}원, 52주 최고가: {high_52w:,}원, 이평선 추세: {trend_state}, MACD: {macd_state}, RSI(14): {rsi_val:.1f}
                [거래량 변동] 금일 거래량: {cur_vol:,}주, 20일 평균 거래량 대비 {vol_ratio:.1f}% 수준 (100% 초과시 평소보다 거래량 터진 것)
                [거버넌스] 시가배당률: {dividend}%
                [매크로] 코스피 시장 상태: {kospi_state}, 원/달러 환율: {usd_krw}원
                """
                
                prompt = f"""
                당신은 프랍 트레이딩 펌의 수석 수석 애널리스트입니다. 다음 제공된 데이터를 바탕으로 14개 항목 투자 리포트를 한국어로 작성하라. 
                제공된 데이터가 있다면 막연한 소리 대신 해당 숫자를 반드시 인용하여 분석할 것.
                제공된 데이터: {report_data}
                
                항목: 1.요약 2.개요 3.재무분석 4.밸류에이션 5.산업/경쟁 6.기술분석(이평선, MACD, 최근 거래량 폭발 여부 반드시 포함) 7.거버넌스 8.매크로 9.촉매 10.베어케이스 11.시나리오 12.점수산출 13.최종판단 14.출처(네이버 금융 명시)
                """
                
                response = model.generate_content(prompt)
                status.update(label="분석 완료!", state="complete", expanded=False)
                st.markdown(response.text)
            except Exception as e:
                st.error(f"리포트 생성 중 에러 발생: {e}")
