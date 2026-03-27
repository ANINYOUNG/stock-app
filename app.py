import streamlit as st
import FinanceDataReader as fdr
import pandas as pd
import numpy as np
import requests
from bs4 import BeautifulSoup
from io import StringIO
import google.generativeai as genai
from datetime import datetime, timedelta

# --- [초기 설정 및 API 키 보안 세팅] ---
st.set_page_config(layout="wide", page_title="AI 퀀트 스캐너 & 애널리스트")

try:
    import config
    API_KEY = config.GEMINI_API_KEY
except ImportError:
    API_KEY = st.secrets["GEMINI_API_KEY"]

genai.configure(api_key=API_KEY)
model = genai.GenerativeModel('gemini-2.5-flash')

# --- [데이터 수집 및 보조 지표 함수] ---
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
        if not df_backup.empty:
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

def detect_candle_pattern(df):
    if len(df) < 20: return "데이터 부족"
    
    df_temp = df.copy()
    df_temp['MA20'] = df_temp['Close'].rolling(20).mean()
    
    today = df_temp.iloc[-1]
    yest = df_temp.iloc[-2]
    
    O, H, L, C, V = today['Open'], today['High'], today['Low'], today['Close'], today['Volume']
    y_O, y_C, y_V = yest['Open'], yest['Close'], yest['Volume']
    ma20 = today['MA20']
    
    body = abs(C - O)
    total_range = H - L
    upper_shadow = H - max(O, C)
    lower_shadow = min(O, C) - L
    
    if total_range == 0: return "⚪ 보합"
    
    if L <= ma20 <= C and C > O and body >= total_range * 0.7:
        return "🔥 20선 지지 장대양봉"
    if y_C > y_O and (y_C - y_O) >= (yest['High'] - yest['Low']) * 0.6 and body <= total_range * 0.1 and V < y_V:
        return "⏳ 눌림목 도지 (에너지 응축)"
    if L > ma20 and upper_shadow >= body * 2 and lower_shadow <= body * 0.5 and C > O:
        return "☄️ 역망치형 (상단 매물 소화)"
    if C < df_temp['Close'].iloc[-5] and lower_shadow >= body * 2 and upper_shadow <= body * 0.5:
        return "🔨 하락 후 망치형 (저점 반등)"
        
    if y_C < y_O and C > O and C > y_O and O < y_C: return "📈 상승 장악형"
    if C > O: return "🔴 양봉"
    elif C < O: return "🔵 음봉"
    else: return "⚪ 보합"

def safe_float(text):
    try:
        if not text or text.strip() in ['-', 'N/A', '']: return 0.0
        return float(text.strip().replace(',', ''))
    except: return 0.0

def get_recent_fin_value(soup, keyword):
    try:
        for th in soup.find_all('th'):
            if keyword in th.text:
                row = th.parent
                tds = row.find_all('td')
                for td in reversed(tds):
                    val = td.text.strip().replace(',', '')
                    if val and val not in ['-', 'N/A']: return float(val)
                break
    except: pass
    return 0.0

def get_frgn_trend(code):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    }
    url_frgn = f"https://finance.naver.com/item/frgn.naver?code={code}"
    res_frgn = requests.get(url_frgn, headers=headers)
    if res_frgn.status_code != 200: return pd.DataFrame()

    html_frgn = res_frgn.content.decode('euc-kr', 'replace') 
    try:
        df_frgn_list = pd.read_html(StringIO(html_frgn))
        df_frgn = pd.DataFrame()
        for df in df_frgn_list:
            if len(df.columns) >= 9:
                df_frgn = df
                break
        df_frgn.columns = range(len(df_frgn.columns))
        df_frgn = df_frgn.dropna(subset=[0]) 
        df_frgn = df_frgn[df_frgn[0].astype(str).str.contains(r'\d{4}\.\d{2}\.\d{2}') == True] 
        
        df_frgn_5days = df_frgn.head(5)[[0, 1, 4, 5, 6, 8]].copy()
        df_frgn_5days.columns = ['날짜', '종가', '거래량', '기관 순매수', '외국인 순매수', '외국인 보유율(%)']
        return df_frgn_5days
    except:
        return pd.DataFrame()

def convert_df_to_csv(df):
    return df.to_csv(index=False, encoding='utf-8-sig')

# --- [단기 기억 장치 초기화] ---
if 'min_marcap' not in st.session_state: st.session_state.min_marcap = 5000
if 'target_per' not in st.session_state: st.session_state.target_per = 15
if 'min_roe' not in st.session_state: st.session_state.min_roe = 10
if 'target_pbr' not in st.session_state: st.session_state.target_pbr = 1.5
if 'max_debt' not in st.session_state: st.session_state.max_debt = 150
if 'target_rsi' not in st.session_state: st.session_state.target_rsi = 70
if 'min_price' not in st.session_state: st.session_state.min_price = 2000 
if 'target_rs' not in st.session_state: st.session_state.target_rs = 5.0
if 'scanned_data' not in st.session_state: st.session_state.scanned_data = None 
if 'watchlist' not in st.session_state: st.session_state.watchlist = [] 

if 'use_marcap' not in st.session_state: st.session_state.use_marcap = True
if 'use_per' not in st.session_state: st.session_state.use_per = True
if 'use_pbr' not in st.session_state: st.session_state.use_pbr = True
if 'use_roe' not in st.session_state: st.session_state.use_roe = True
if 'use_debt' not in st.session_state: st.session_state.use_debt = True
if 'use_op' not in st.session_state: st.session_state.use_op = True 
if 'use_rsi' not in st.session_state: st.session_state.use_rsi = True
if 'use_min_price' not in st.session_state: st.session_state.use_min_price = True 
if 'use_vol_surge' not in st.session_state: st.session_state.use_vol_surge = False
if 'use_rs' not in st.session_state: st.session_state.use_rs = False 

if 'compare_results_df' not in st.session_state: st.session_state.compare_results_df = None
if 'backtest_results_df' not in st.session_state: st.session_state.backtest_results_df = None
if 'chat_history' not in st.session_state: st.session_state.chat_history = []
if 'current_target_code' not in st.session_state: st.session_state.current_target_code = None

# --- [사이드바: 종목 선별 기준 및 AI] ---
st.sidebar.header("🔍 1~3단계: 전체 시장 스캔 필터")

scan_option = st.sidebar.selectbox(
    "검사할 종목 범위", 
    ["시총 상위 100개", "시총 상위 500개", "시총 상위 1000개", "시총 하위 50% (소형주)", "전체 종목 (약 2500개 - 최대 15분)"], 
    index=0
)

df_krx_full = get_krx_data()

if df_krx_full.empty or 'Sector' not in df_krx_full.columns:
    sectors_list = []
    df_krx_full['Sector'] = '미분류' 
else:
    sectors_list = [s for s in df_krx_full['Sector'].unique() if isinstance(s, str)]
    
sectors_list.sort()
default_excludes = [s for s in ['금융업', '보험업'] if s in sectors_list]
excluded_sectors = st.sidebar.multiselect("🚫 제외할 업종", options=sectors_list, default=default_excludes, help="은행, 보험 등은 부채비율이 무조건 높게 나오므로 제외하는 것이 좋습니다.")

st.sidebar.divider()

col1, col2 = st.sidebar.columns(2)
filter_keys = ['use_marcap', 'use_min_price', 'use_per', 'use_pbr', 'use_roe', 'use_debt', 'use_op', 'use_rsi', 'use_vol_surge', 'use_rs']

if col1.button("✅ 필터 모두 켜기", use_container_width=True):
    for key in filter_keys: st.session_state[key] = True
    st.rerun()
if col2.button("❌ 필터 모두 끄기", use_container_width=True):
    for key in filter_keys: st.session_state[key] = False
    st.rerun()

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
    
    st.session_state.use_vol_surge = st.checkbox("🔥 오늘 거래량 2배 폭발 종목", value=st.session_state.use_vol_surge)

    st.session_state.use_rs = st.checkbox("🦅 최소 상대강도(RS) 적용", value=st.session_state.use_rs)
    st.session_state.target_rs = st.number_input("최소 RS (%)", value=st.session_state.target_rs, step=1.0, disabled=not st.session_state.use_rs)

scan_button = st.sidebar.button("🎯 전체 시장 스캐너 가동", type="primary", use_container_width=True)

st.sidebar.divider()
st.sidebar.header("⚡ 4단계: 관심종목 쾌속 스캔")
st.session_state.watchlist = st.sidebar.multiselect("장바구니 (검색하여 추가)", options=df_krx_full['Name'].tolist(), default=st.session_state.watchlist)
direct_scan_button = st.sidebar.button("🚀 선택 종목 다이렉트 분석", use_container_width=True)

st.sidebar.divider()
st.sidebar.subheader("🤖 미니 AI 용어 사전")
st.sidebar.caption("어려운 금융 용어를 초보자 눈높이에서 아주 쉽게 비유해서 설명해 드립니다.")
term_query = st.sidebar.text_input("질문 입력 (예: 골든크로스가 뭐야?)")

if term_query:
    with st.sidebar.spinner("AI가 알기 쉽게 설명 중..."):
        try:
            prompt = f"당신은 친절한 주식 멘토입니다. 초보 주식 투자자가 '{term_query}'에 대해 질문했습니다. 전문 용어를 최소화하고, 일상 생활의 비유를 들어서 3~4문장으로 아주 쉽게 설명해 주세요."
            term_ans = model.generate_content(prompt)
            st.sidebar.info(term_ans.text)
        except Exception as e:
            st.sidebar.error("설명을 불러오는데 실패했습니다.")

# --- [메인 화면 로직: 매크로 풍향계 & 스캔] ---
st.title("📈 AI 심층 분석 시스템")
st.markdown("표의 맨 위 **기둥(글자)**에 마우스를 올리시면 초보자를 위한 꿀팁 설명이 나타납니다! ✨")

now_kst = datetime.utcnow() + timedelta(hours=9)
current_time_kst = now_kst.strftime('%Y년 %m월 %d일 %H:%M:%S')

try:
    df_kospi = fdr.DataReader('KS11').tail(20)
    cur_kospi = df_kospi['Close'].iloc[-1]
    ma20_kospi = df_kospi['Close'].mean()
    if cur_kospi >= ma20_kospi:
        st.success(f"🧭 **오늘의 시장 풍향계:** 🟢 강세장 (코스피 20일선 돌파 유지 중) | 현재가: {cur_kospi:,.2f}pt\n\n💡 **AI 전략 조언:** 시장에 돈이 돌고 있습니다. RSI 70 이하의 정배열 우량주를 적극적으로 공략하세요!")
    else:
        st.error(f"🧭 **오늘의 시장 풍향계:** 🔴 약세장 (코스피 20일선 이탈) | 현재가: {cur_kospi:,.2f}pt\n\n💡 **AI 전략 조언:** 시장이 조정을 받고 있습니다. 가치평가 대비 매우 저렴하고 하락방어율이 좋은 종목만 보수적으로 접근하세요.")
except: pass

if scan_button or direct_scan_button:
    st.session_state.scanned_data = None 
    st.session_state.compare_results_df = None
    st.session_state.backtest_results_df = None
    
    df_krx = get_krx_data()
    
    if direct_scan_button:
        if not st.session_state.watchlist:
            st.warning("⚠️ 왼쪽 메뉴에서 관심종목을 먼저 추가해주세요.")
            st.stop()
        filtered_by_cap = df_krx[df_krx['Name'].isin(st.session_state.watchlist)]
    else:
        with st.spinner('1차: 선택한 범위의 종목을 로드 중입니다...'):
            if excluded_sectors and 'Sector' in df_krx.columns: 
                df_krx = df_krx[~df_krx['Sector'].isin(excluded_sectors)]
            
            df_krx = df_krx.sort_values('Marcap', ascending=False)
            total_count = len(df_krx)
            
            if scan_option == "시총 상위 100개": filtered_by_cap = df_krx.head(100)
            elif scan_option == "시총 상위 500개": filtered_by_cap = df_krx.head(500)
            elif scan_option == "시총 상위 1000개": filtered_by_cap = df_krx.head(1000)
            elif scan_option == "시총 하위 50% (소형주)":
                half_idx = total_count // 2
                filtered_by_cap = df_krx.iloc[half_idx:] 
            else: filtered_by_cap = df_krx

            if st.session_state.use_marcap:
                min_marcap_won = st.session_state.min_marcap * 100000000
                filtered_by_cap = filtered_by_cap[filtered_by_cap['Marcap'] >= min_marcap_won]

    if not filtered_by_cap.empty:
        progress_text = f"2차: 네이버 금융 재무 데이터 수집 및 S-RIM 가치평가 중..."
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
                current_price = getattr(row, 'Price', int(row.Marcap / float(row.Stocks)) if getattr(row, 'Stocks', 0) else 0)
                
                required_return = 8.0 
                bps = current_price / pbr if pbr > 0 else 0
                if bps > 0 and roe > 0:
                    s_rim_price = bps + bps * ((roe - required_return) / required_return)
                    s_rim_price = max(0, int(s_rim_price)) 
                else:
                    s_rim_price = 0

                fin_results.append({
                    'Code': code, 'Name': row.Name, '업종': row.Sector if 'Sector' in row._fields else '미분류', 
                    '시가총액(억)': int(row.Marcap // 100000000), 
                    '현재가': current_price, 'S-RIM적정가': s_rim_price,
                    'PER': round(per, 2), 'PBR': round(pbr, 2), 'ROE': round(roe, 2),
                    '부채비율(%)': round(debt_ratio, 2), '영업이익(억)': op_profit, '배당(%)': dvr
                })
            except: pass 
            progress_bar.progress((idx + 1) / total_stocks, text=f"{progress_text} ({idx+1}/{total_stocks} 완료)")
        
        progress_bar.empty()
        df_fin = pd.DataFrame(fin_results)
        
        if not df_fin.empty:
            if direct_scan_button:
                survivors_df = df_fin.copy() 
            else:
                mask = pd.Series(True, index=df_fin.index)
                if st.session_state.use_per: mask = mask & (df_fin['PER'] > 0) & (df_fin['PER'] <= st.session_state.target_per)
                if st.session_state.use_pbr: mask = mask & (df_fin['PBR'] > 0) & (df_fin['PBR'] <= st.session_state.target_pbr)
                if st.session_state.use_roe: mask = mask & (df_fin['ROE'] >= st.session_state.min_roe)
                if st.session_state.use_debt: mask = mask & (df_fin['부채비율(%)'] <= st.session_state.max_debt)
                if st.session_state.use_op: mask = mask & (df_fin['영업이익(억)'] > 0)
                if st.session_state.use_min_price: mask = mask & (df_fin['현재가'] >= st.session_state.min_price)
                survivors_df = df_fin[mask].copy()
        else:
            survivors_df = pd.DataFrame()

        if not survivors_df.empty:
            progress_text2 = f"3차: 차트 분석 및 거래량/수급에너지 필터 진행 중..."
            progress_bar2 = st.progress(0, text=progress_text2)
            final_results = []
            total_survivors = len(survivors_df)
            survivors_records = survivors_df.to_dict('records')
            
            # 💡 [핵심 업그레이드] 코스피 데이터를 넉넉히(250일치) 가져와서 정확한 날짜 매칭 준비
            try:
                df_kospi_rs = fdr.DataReader('KS11').tail(250)
            except:
                df_kospi_rs = pd.DataFrame()
            
            for idx, row_dict in enumerate(survivors_records):
                try:
                    df_price = fdr.DataReader(row_dict['Code']).tail(250) 
                    if not df_price.empty:
                        high_52w = df_price['High'].max()
                        low_52w = df_price['Low'].min()
                        
                        if len(df_price) >= 20:
                            avg_vol_20 = df_price['Volume'].rolling(window=20).mean().iloc[-2]
                            cur_vol = df_price['Volume'].iloc[-1]
                            vol_ratio = (cur_vol / avg_vol_20) * 100 if avg_vol_20 > 0 else 0
                            
                            # 💡 [핵심 업그레이드] '정확한 날짜' 동기화 RS 계산 로직
                            if not df_kospi_rs.empty:
                                date_today = df_price.index[-1]
                                date_20days_ago = df_price.index[-20]
                                
                                stock_today = df_price['Close'].iloc[-1]
                                stock_20days_ago = df_price['Close'].iloc[-20]
                                
                                # 코스피 지수에서 정확히 해당 날짜의 값을 가져옴 (휴장일 보정을 위해 asof 사용)
                                kospi_today = df_kospi_rs['Close'].asof(date_today)
                                kospi_20days_ago = df_kospi_rs['Close'].asof(date_20days_ago)
                                
                                ret_1m_stock = (stock_today - stock_20days_ago) / stock_20days_ago * 100
                                ret_1m_kospi = (kospi_today - kospi_20days_ago) / kospi_20days_ago * 100
                                rs_1m = ret_1m_stock - ret_1m_kospi
                            else:
                                rs_1m = -999
                            
                            df_5d = df_price.tail(5)
                            up_vol = df_5d[df_5d['Close'] > df_5d['Open']]['Volume'].sum()
                            down_vol = df_5d[df_5d['Close'] < df_5d['Open']]['Volume'].sum()
                            if down_vol == 0:
                                vp_ratio = 999 if up_vol > 0 else 0
                            else:
                                vp_ratio = (up_vol / down_vol) * 100
                        else: 
                            vol_ratio = 0
                            rs_1m = -999 
                            vp_ratio = 0
                        
                        pass_vol_check = True
                        if st.session_state.use_vol_surge and vol_ratio < 200: pass_vol_check = False
                        
                        pass_rs_check = True
                        if st.session_state.use_rs and rs_1m < st.session_state.target_rs: pass_rs_check = False
                        
                        if pass_vol_check and pass_rs_check:
                            rsi_val = calculate_rsi(df_price).iloc[-1]
                            if direct_scan_button or not st.session_state.use_rsi or rsi_val <= st.session_state.target_rsi:
                                row_dict['RSI'] = round(rsi_val, 1)
                                row_dict['RS(%)'] = round(rs_1m, 1) 
                                row_dict['거래량(%)'] = f"{int(vol_ratio)}%" 
                                row_dict['수급에너지(VP)'] = f"{int(vp_ratio)}%" 
                                row_dict['52주 최고'] = int(high_52w) 
                                row_dict['52주 최저'] = int(low_52w)  
                                final_results.append(row_dict)
                except: pass
                progress_bar2.progress((idx + 1) / total_survivors, text=f"{progress_text2} ({idx+1}/{total_survivors})")
            
            progress_bar2.empty()
            if final_results: st.session_state.scanned_data = pd.DataFrame(final_results).sort_values(by='RS(%)', ascending=False)
            else: st.session_state.scanned_data = pd.DataFrame()

# --- [메인 화면 출력: 탭(Tab) 기반 UI 레이아웃] ---
if st.session_state.scanned_data is not None and not st.session_state.scanned_data.empty:
    final_df = st.session_state.scanned_data
    
    st.caption(f"🕒 **데이터 기준 일시 (KST):** {current_time_kst}")
    
    tab1, tab2, tab3 = st.tabs(["📊 1. 검색 결과 리스트", "🚦 2. 정밀 분석 대시보드", "🤖 3. AI 리포트 & 호가창"])
    
    # --- 탭 1: 검색 결과 리스트 ---
    with tab1:
        st.subheader(f"✅ 조건검색 결과 ({len(final_df)}개 발견)")
        display_df = final_df.copy()
        
        if '시가총액(억)' in display_df.columns: display_df['시가총액(억)'] = display_df['시가총액(억)'].apply(lambda x: f"{x:,}")
        if '영업이익(억)' in display_df.columns: display_df['영업이익(억)'] = display_df['영업이익(억)'].apply(lambda x: f"{int(x):,}") 
        if '현재가' in display_df.columns: display_df['현재가'] = display_df['현재가'].apply(lambda x: f"{int(x):,}") 
        if 'S-RIM적정가' in display_df.columns: display_df['S-RIM적정가'] = display_df['S-RIM적정가'].apply(lambda x: f"{int(x):,}") 
        if '52주 최고' in display_df.columns: display_df['52주 최고'] = display_df['52주 최고'].apply(lambda x: f"{int(x):,}") 
        if '52주 최저' in display_df.columns: display_df['52주 최저'] = display_df['52주 최저'].apply(lambda x: f"{int(x):,}") 
        
        base_cols = ['Code', 'Name', '업종', '시가총액(억)', '현재가', 'S-RIM적정가']
        if '52주 최저' in display_df.columns: base_cols.append('52주 최저')
        if '52주 최고' in display_df.columns: base_cols.append('52주 최고')
        
        display_cols = base_cols + ['PER', 'PBR', 'ROE', '부채비율(%)', '영업이익(억)']
        if 'RSI' in display_df.columns: display_cols.append('RSI')
        if 'RS(%)' in display_df.columns: display_cols.append('RS(%)') 
        if '거래량(%)' in display_df.columns: display_cols.append('거래량(%)') 
        if '수급에너지(VP)' in display_df.columns: display_cols.append('수급에너지(VP)') 
        
        st.dataframe(display_df[display_cols], use_container_width=True, hide_index=True, 
            column_config={
                "S-RIM적정가": st.column_config.Column(help="사경인 회계사의 잔여이익모델로 계산한 이 기업의 '진짜 가치(적정 주가)'입니다."),
                "PER": st.column_config.Column(help="주가수익비율. 회사가 버는 돈 대비 주가가 얼마인지 나타냅니다. 낮을수록 저평가!"),
                "PBR": st.column_config.Column(help="주가순자산비율. 회사가 가진 재산 대비 주가가 얼마인지 나타냅니다. 1보다 작으면 재산보다 주가가 싼 상태!"),
                "ROE": st.column_config.Column(help="자기자본이익률. 내 돈을 굴려서 1년에 몇 %의 수익을 냈는지 나타냅니다. 높을수록 장사 잘하는 기업!"),
                "부채비율(%)": st.column_config.Column(help="회사의 빚입니다. 보통 150% 이하를 안전하다고 봅니다."),
                "RSI": st.column_config.Column(help="상대강도지수. 30 이하면 너무 많이 떨어진 과매도(바닥권), 70 이상이면 너무 많이 오른 과매수(천장권)를 의미합니다."),
                "RS(%)": st.column_config.Column(help="상대강도. 코스피 지수보다 몇 %나 더 강하게 올랐는지(주도주 여부) 보여줍니다."),
                "거래량(%)": st.column_config.Column(help="최근 20일 평균 대비 오늘 거래량이 몇 % 터졌는지 보여줍니다. 세력의 진입 흔적입니다."),
                "수급에너지(VP)": st.column_config.Column(help="최근 5일간 양봉(상승) 거래량 합이 음봉(하락) 거래량 합의 몇 배인지 나타냅니다. 100% 이상이면 매수세가 더 강하다는 뜻입니다.")
            }
        )
        
        export_df = display_df[display_cols].copy()
        scan_date_str = now_kst.strftime('%Y-%m-%d')
        scan_time_str = now_kst.strftime('%H:%M')
        
        export_df.insert(0, '스캔시간', scan_time_str)
        export_df.insert(0, '스캔일자', scan_date_str)
        
        csv = convert_df_to_csv(export_df)
        file_date_suffix = now_kst.strftime('%Y%m%d')
        download_filename = f'quant_watchlist_{file_date_suffix}.csv'
        
        st.download_button(label=f"📥 엑셀(CSV)로 리스트 다운로드 ({download_filename})", data=csv, file_name=download_filename, mime='text/csv')
    
    # --- 탭 2: 정밀 분석 대시보드 ---
    with tab2:
        st.info("💡 종목을 선택하여 볼린저밴드, 세력 평단가, 52주 신고가 모멘텀 등을 한눈에 비교하세요.")
        selected_names = st.multiselect("비교할 종목들을 선택하세요", final_df['Name'].tolist(), default=final_df['Name'].tolist()[:3])
        
        if st.button("🚀 선택 종목 정밀 비교", use_container_width=True) and selected_names:
            with st.spinner('차트 지표 및 세력 평단가/주도력 데이터를 융합 분석 중입니다...'):
                compare_results = []
                backtest_results = []
                
                try: df_kospi = fdr.DataReader('KS11').tail(400)
                except: df_kospi = pd.DataFrame()
                
                for name in selected_names:
                    row = final_df[final_df['Name'] == name].iloc[0]
                    code = row['Code']
                    
                    df_price = fdr.DataReader(code).tail(400)
                    if df_price.empty: continue

                    df_price['MA20'] = df_price['Close'].rolling(window=20).mean()
                    df_price['MA60'] = df_price['Close'].rolling(window=60).mean()
                    df_price['MA120'] = df_price['Close'].rolling(window=120).mean()
                    
                    exp1 = df_price['Close'].ewm(span=12, adjust=False).mean()
                    exp2 = df_price['Close'].ewm(span=26, adjust=False).mean()
                    macd_series = exp1 - exp2
                    signal_series = macd_series.ewm(span=9, adjust=False).mean()
                    
                    if len(df_price) >= 20:
                        avg_vol_20 = df_price['Volume'].rolling(window=20).mean().iloc[-2]
                        cur_vol = df_price['Volume'].iloc[-1]
                        vol_ratio = (cur_vol / avg_vol_20) * 100 if avg_vol_20 > 0 else 0
                        if vol_ratio >= 200: vol_sig = f"🔥 폭발 ({int(vol_ratio)}%)"
                        elif vol_ratio >= 120: vol_sig = f"🟢 증가 ({int(vol_ratio)}%)"
                        else: vol_sig = f"⚪ 평이 ({int(vol_ratio)}%)"
                        
                        std20 = df_price['Close'].rolling(window=20).std().iloc[-1]
                        ma20_cur = df_price['MA20'].iloc[-1]
                        upper_band = ma20_cur + (std20 * 2)
                        lower_band = ma20_cur - (std20 * 2)
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
                        if idx < -len(df_price) + 1: return "-", "-"
                        ma20, ma60, ma120 = df_price['MA20'].iloc[idx], df_price['MA60'].iloc[idx], df_price['MA120'].iloc[idx]
                        if pd.isna(ma120): trend = "알수없음"
                        elif ma20 > ma60 > ma120: trend = "🟢 정배열"
                        elif ma20 < ma60 < ma120: trend = "🔴 역배열"
                        else: trend = "🟡 혼조세"
                        
                        m_cur, s_cur = macd_series.iloc[idx], signal_series.iloc[idx]
                        m_prev, s_prev = macd_series.iloc[idx-1], signal_series.iloc[idx-1]
                        
                        if pd.isna(m_cur) or pd.isna(s_cur): macd_sig = "알수없음"
                        else:
                            if m_prev <= s_prev and m_cur > s_cur: macd_sig = f"🚀 골든크로스 (M:{m_cur:.0f} > S:{s_cur:.0f})"
                            elif m_prev >= s_prev and m_cur < s_cur: macd_sig = f"🔻 데드크로스 (M:{m_cur:.0f} < S:{s_cur:.0f})"
                            elif m_cur > s_cur: macd_sig = f"🟢 매수우위 (M:{m_cur:.0f} > S:{s_cur:.0f})"
                            else: macd_sig = f"🔴 매도우위 (M:{m_cur:.0f} < S:{s_cur:.0f})"
                        return trend, macd_sig

                    current_price = df_price['Close'].iloc[-1]
                    high_52w = df_price['High'].tail(250).max() 
                    
                    breakout_ratio = (current_price / high_52w) * 100 if high_52w > 0 else 0
                    if breakout_ratio >= 98: momentum_sig = "🦅 신고가 돌파"
                    elif breakout_ratio >= 90: momentum_sig = "↗️ 돌파 시도"
                    else: momentum_sig = "⚪ 하단 횡보"
                    
                    cur_trend, cur_macd = get_historical_signals(-1)
                    drawdown = ((current_price - high_52w) / high_52w) * 100
                    dd_signal = f"🟢 {drawdown:.1f}%" if drawdown > -20 else f"🔴 {drawdown:.1f}%"
                    
                    rs_sig = "-"
                    vwap_sig = "-"
                    if not df_kospi.empty and len(df_price) >= 120 and len(df_kospi) >= 20:
                        try:
                            # 💡 여기에도 날짜 동기화 로직 적용 완료
                            date_today = df_price.index[-1]
                            date_20days_ago = df_price.index[-20]
                            
                            kospi_today = df_kospi['Close'].asof(date_today)
                            kospi_20days_ago = df_kospi['Close'].asof(date_20days_ago)
                            
                            ret_1m_stock = (current_price - df_price['Close'].iloc[-20]) / df_price['Close'].iloc[-20] * 100
                            ret_1m_kospi = (kospi_today - kospi_20days_ago) / kospi_20days_ago * 100
                            rs_1m = ret_1m_stock - ret_1m_kospi
                            if rs_1m > 5: rs_sig = f"🔥 주도주 (+{rs_1m:.1f}%)"
                            elif rs_1m < -5: rs_sig = f"🧊 소외주 ({rs_1m:.1f}%)"
                            else: rs_sig = f"⚪ 시장동기화 ({rs_1m:.1f}%)"
                            
                            df_20 = df_price.tail(20).copy()
                            vwap_20 = (df_20['Close'] * df_20['Volume']).sum() / df_20['Volume'].sum()
                            
                            prices_120 = df_price.tail(120)['Close'].values
                            volumes_120 = df_price.tail(120)['Volume'].values
                            hist, bins = np.histogram(prices_120, bins=10, weights=volumes_120)
                            max_vol_idx = np.argmax(hist)
                            heaviest_price = (bins[max_vol_idx] + bins[max_vol_idx+1]) / 2
                            
                            vwap_sig = f"평단: {int(vwap_20):,}원 / 매물대: {int(heaviest_price):,}원"
                        except: pass
                    
                    compare_results.append({
                        '종목명': name, '현재가': f"{int(current_price):,}원",
                        '① 이평선': cur_trend, '② MACD': cur_macd, 
                        '③ 방어율(눌림)': dd_signal, '④ 거래량': vol_sig, 
                        '⑤ 캔들': candle_sig, '⑥ 볼린저': bb_sig, '⑦ 모멘텀': momentum_sig,
                        '⑧ 주도력(RS)': rs_sig, '⑨ 세력평단가': vwap_sig
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
                    st.session_state.compare_results_df = pd.DataFrame(compare_results)
                    st.session_state.backtest_results_df = pd.DataFrame(backtest_results)

        if st.session_state.compare_results_df is not None and not st.session_state.compare_results_df.empty:
            st.dataframe(st.session_state.compare_results_df, use_container_width=True, hide_index=True,
                column_config={
                    "① 이평선": st.column_config.Column(help="이동평균선의 흐름. 정배열은 상승 추세, 역배열은 하락 추세를 의미합니다."),
                    "② MACD": st.column_config.Column(help="빨간색(골든크로스)이면 상승 추세 시작, 파란색(데드크로스)이면 하락 추세 시작을 의미합니다."),
                    "③ 방어율(눌림)": st.column_config.Column(help="52주 최고가 대비 현재 얼마나 떨어졌는지 보여줍니다. 폭락장에서도 덜 떨어지는 주식이 강한 주식입니다."),
                    "⑤ 캔들": st.column_config.Column(help="오늘의 캔들 패턴. '20선 지지 장대양봉', '눌림목 도지' 등은 강력한 매수 타점입니다."),
                    "⑥ 볼린저": st.column_config.Column(help="주가가 움직이는 도로(밴드). 하단선 터치 시 반등 확률이 높고, 밴드가 좁아지면 곧 크게 위아래로 터질 징조입니다."),
                    "⑧ 주도력(RS)": st.column_config.Column(help="코스피 지수 대비 얼마나 더 올랐는지(상대강도). 폭락장에서도 혼자 버티는 '진짜 주도주'를 찾습니다."),
                    "⑨ 세력평단가": st.column_config.Column(help="거래량을 실어 평균을 낸 '세력의 20일 평단가(VWAP)'와 120일간 가장 많이 거래된 '콘크리트 매물대'입니다.")
                }
            )
            st.markdown("---")
            st.subheader("⏪ 미니 백테스팅 (과거 매수 시점의 주가와 수익률)")
            st.dataframe(st.session_state.backtest_results_df, use_container_width=True, hide_index=True)

    # --- 탭 3: AI 리포트 및 수급 분석 ---
    with tab3:
        target_name = st.selectbox("리포트를 생성할 최종 타겟 종목 1개를 선택하세요", final_df['Name'].tolist())
        target_code = final_df[final_df['Name'] == target_name]['Code'].values[0]
        
        if st.session_state.current_target_code != target_code:
            st.session_state.chat_history = []
            st.session_state.current_target_code = target_code
        
        col1, col2 = st.columns(2)
        with col1:
            report_btn = st.button(f"📝 {target_name} AI 리포트 생성 (세력 수급 분석 포함)", use_container_width=True)
        with col2:
            naver_url = f"https://stock.naver.com/domestic/stock/{target_code}/price"
            st.link_button(f"🔴 {target_name} 실시간 호가창 보기 (새 창)", naver_url, use_container_width=True)
        
        if report_btn:
            st.session_state.chat_history = []
            
            with st.spinner('세력(기관/외국인)의 최근 5일 매매 흔적을 추적 중입니다...'):
                df_trend = get_frgn_trend(target_code)
                trend_str_for_ai = "수급 데이터 없음"
                
                if not df_trend.empty:
                    st.markdown("##### 🕵️‍♂️ [참고] 최근 5일 기관/외국인 수급 흐름표")
                    display_trend = df_trend.copy()
                    for col in ['종가', '거래량', '기관 순매수', '외국인 순매수']:
                        display_trend[col] = pd.to_numeric(display_trend[col], errors='coerce').fillna(0).apply(lambda x: f"{int(x):,}")
                    
                    st.dataframe(display_trend, hide_index=True, use_container_width=True,
                        column_config={
                            "기관 순매수": st.column_config.Column(help="국내 펀드매니저, 연기금 등이 순수하게 주식을 사들인 수량"),
                            "외국인 순매수": st.column_config.Column(help="외국인 투자자가 순수하게 주식을 사들인 수량")
                        }
                    )
                    st.divider()
                    
                    trend_str_for_ai = df_trend.to_csv(index=False)
            
            with st.status(f"{target_name} AI 종합 리포트 작성 중... (거시경제, 리스크, 세력수급 포함)", expanded=True) as status:
                try:
                    row = final_df[final_df['Name'] == target_name].iloc[0]
                    df_target = fdr.DataReader(target_code).tail(250)
                    cur_price = df_target['Close'].iloc[-1]
                    cur_vol = df_target['Volume'].iloc[-1]
                    
                    avg_vol_20 = df_target['Volume'].rolling(window=20).mean().iloc[-2] if len(df_target) >= 20 else 0
                    vol_ratio = (cur_vol / avg_vol_20) * 100 if avg_vol_20 > 0 else 0
                    
                    high_52w = df_target['High'].max()
                    low_52w = df_target['Low'].min()
                    
                    ma20 = df_target['Close'].rolling(window=20).mean().iloc[-1]
                    ma60 = df_target['Close'].rolling(window=60).mean().iloc[-1]
                    ma120 = df_target['Close'].rolling(window=120).mean().iloc[-1]
                    trend_state = "정배열(상승추세)" if ma20 > ma60 > ma120 else ("역배열(하락추세)" if ma20 < ma60 < ma120 else "혼조세")
                    
                    exp1 = df_target['Close'].ewm(span=12, adjust=False).mean()
                    exp2 = df_target['Close'].ewm(span=26, adjust=False).mean()
                    macd_s = exp1 - exp2
                    sig_s = macd_s.ewm(span=9, adjust=False).mean()
                    
                    if len(macd_s) >= 2:
                        m_cur, s_cur = macd_s.iloc[-1], sig_s.iloc[-1]
                        m_prev, s_prev = macd_s.iloc[-2], sig_s.iloc[-2]
                        if m_prev <= s_prev and m_cur > s_cur: macd_state = f"골든크로스 발생 (M:{m_cur:.0f} > S:{s_cur:.0f})"
                        elif m_prev >= s_prev and m_cur < s_cur: macd_state = f"데드크로스 발생 (M:{m_cur:.0f} < S:{s_cur:.0f})"
                        elif m_cur > s_cur: macd_state = f"매수우위 (M:{m_cur:.0f} > S:{s_cur:.0f})"
                        else: macd_state = f"매도우위 (M:{m_cur:.0f} < S:{s_cur:.0f})"
                    else:
                        macd_state = "데이터 부족"
                        
                    rsi_val = calculate_rsi(df_target).iloc[-1]
                    candle_state = detect_candle_pattern(df_target)

                    if len(df_target) >= 20:
                        std20 = df_target['Close'].rolling(window=20).std().iloc[-1]
                        upper_band = ma20 + (std20 * 2)
                        lower_band = ma20 - (std20 * 2)
                        bandwidth = (upper_band - lower_band) / ma20 if ma20 > 0 else 0
                        
                        if cur_price <= lower_band * 1.02: bb_state = "하한선 터치 (통계적 과매도, 반등 지지선 부근)"
                        elif cur_price >= upper_band * 0.98: bb_state = "상한선 터치 (통계적 과매수, 저항선 부근)"
                        elif bandwidth < 0.10: bb_state = "밴드 수축/스퀴즈 (에너지 응축 중, 큰 변동성 예상)"
                        else: bb_state = "밴드 중심부 순항 중"
                        
                        bb_price_info = f"상단(저항선): {int(upper_band):,}원 / 하단(지지선): {int(lower_band):,}원"
                    else: 
                        bb_state = "데이터 부족"
                        bb_price_info = "데이터 부족"
                    
                    breakout_ratio = (cur_price / high_52w) * 100 if high_52w > 0 else 0
                    if breakout_ratio >= 98: momentum_state = "52주 신고가 돌파 (강한 상승 모멘텀)"
                    elif breakout_ratio >= 90: momentum_state = "52주 신고가 근접 (돌파 시도 중)"
                    else: momentum_state = "박스권 하단 혹은 하락 추세 중"

                    df_kospi = fdr.DataReader('KS11').tail(20)
                    kospi_state = "강세장" if df_kospi['Close'].iloc[-1] > df_kospi['Close'].mean() else "약세장"
                    try: df_usd = fdr.DataReader('USD/KRW').tail(1); usd_krw = df_usd['Close'].iloc[0]
                    except: usd_krw = "데이터 없음"

                    dividend = row.get('배당(%)', 0.0)
                    s_rim_val = row.get('S-RIM적정가', 0)
                    
                    report_data = f"""
                    [종목 정보] {target_name} (코드: {target_code})
                    [재무/가치] PER: {row['PER']}배, PBR: {row['PBR']}배, ROE: {row['ROE']}%, 부채비율: {row['부채비율(%)']}%, 시가총액: {row['시가총액(억)']}억원, 최근 영업이익: {row['영업이익(억)']}억원
                    [가치 평가] 현재가: {cur_price:,}원, S-RIM 적정주가(요구수익률 8% 가정): {int(s_rim_val):,}원
                    [기술 분석] 52주 최고가: {high_52w:,}원, 52주 최저가: {low_52w:,}원, 이평선 추세: {trend_state}, MACD: {macd_state}, RSI(14): {rsi_val:.1f}
                    [볼린저 밴드] 현재 위치: {bb_state}, 구체적 가격 수치: {bb_price_info}
                    [캔들 패턴] 오늘의 캔들: {candle_state}
                    [수급 에너지] 최근 5일 매수/매도 거래량 비율(VP): {row.get('수급에너지(VP)', '데이터없음')}
                    [모멘텀/리스크] 52주 신고가/신저가 모멘텀 상태: {momentum_state}
                    [거버넌스] 시가배당률: {dividend}%
                    [매크로] 코스피 시장 상태: {kospi_state}, 원/달러 환율: {usd_krw}원
                    [★세력 수급 추이 (최근 5일)]\n{trend_str_for_ai}
                    """
                    
                    prompt = f"""
                    당신은 프랍 트레이딩 펌의 수석 애널리스트입니다. 제공된 데이터를 바탕으로 15개 항목 투자 리포트를 작성하라. 
                    제공된 데이터가 있다면 막연한 소리 대신 해당 숫자를 반드시 인용하여 분석할 것. 
                    (특히 '현재가'와 'S-RIM 적정주가'의 괴리율, 그리고 '[★세력 수급 추이]' 표를 분석하여 기관과 외국인이 최근 5일간 매집 중인지 이탈 중인지를 반드시 구체적으로 코멘트할 것)
                    제공된 데이터: {report_data}
                    항목: 1.요약 2.개요 3.재무분석 4.밸류에이션(S-RIM 적정가 포함) 5.산업/경쟁 6.기술 및 수급 분석(이평선, 거래량, 볼린저밴드, 수급에너지(VP), 기관/외국인 수급 방향성 필수 포함) 7.거버넌스 8.매크로 9.리스크 10.베어케이스 11.시나리오 12.점수산출 13.최종판단 14.출처(네이버 금융) 15.실전 매매 전략 (호가창 및 리스크 관리)
                    
                    [15번 항목 추가 특별 지침]
                    - 리포트 화면 우측의 '실시간 호가창 보기' 링크를 통해 진입 시 매도 잔량이 매수 잔량보다 많은지 반드시 확인하라는 실전 트레이딩 팁을 포함할 것.
                    - 어떠한 타점이라도 진입가 대비 '-2%의 기계적 손절 라인'을 엄격하게 설정하여 리스크를 제어하라는 강력한 경고 문구를 반드시 추가할 것.
                    """
                    
                    response = model.generate_content(prompt)
                    
                    st.session_state.chat_history = [
                        {"role": "user", "parts": [prompt]}, 
                        {"role": "model", "parts": [response.text]}
                    ]
                    
                    status.update(label="분석 완료!", state="complete", expanded=False)
                except Exception as e:
                    st.error(f"리포트 생성 중 에러 발생: {e}")

        if st.session_state.chat_history:
            main_report_text = st.session_state.chat_history[1]["parts"][0]
            report_date_suffix = now_kst.strftime('%Y%m%d')
            report_filename = f"{target_name}_AI심층리포트_{report_date_suffix}.md"
            
            st.download_button(
                label=f"📥 {target_name} AI 리포트 다운로드 (.md 형식)", 
                data=main_report_text, 
                file_name=report_filename, 
                mime="text/markdown",
                use_container_width=True
            )
            
            st.divider()
            st.subheader(f"🗣️ {target_name}에 대해 꼬리 질문을 해보세요!")
            
            for msg in st.session_state.chat_history[1:]:
                with st.chat_message("ai" if msg["role"] == "model" else "user"):
                    st.markdown(msg["parts"][0])
            
            if user_q := st.chat_input(f"{target_name}의 최근 세력 흐름이 어때?"):
                with st.chat_message("user"):
                    st.markdown(user_q)
                st.session_state.chat_history.append({"role": "user", "parts": [user_q]})
                
                with st.chat_message("ai"):
                    with st.spinner("AI가 분석 데이터를 되짚어보고 있습니다..."):
                        chat = model.start_chat(history=st.session_state.chat_history[:-1])
                        ans = chat.send_message(user_q)
                        st.markdown(ans.text)
                        
                st.session_state.chat_history.append({"role": "model", "parts": [ans.text]})
