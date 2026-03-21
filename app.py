import streamlit as st
import FinanceDataReader as fdr
import pandas as pd
import numpy as np
import requests
from bs4 import BeautifulSoup
import re
import google.generativeai as genai
import io
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

# 한국거래소 데이터 캐싱
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

def detect_candle_pattern(df):
    if len(df) < 2: return "데이터 부족"
    today, yest = df.iloc[-1], df.iloc[-2]
    O, H, L, C = today['Open'], today['High'], today['Low'], today['Close']
    y_O, y_C = yest['Open'], yest['Close']
    body = abs(C - O)
    total_range = H - L
    upper_shadow, lower_shadow = H - max(O, C), min(O, C) - L
    if total_range == 0: return "⚪ 보합"
    if y_C < y_O and C > O and C > y_O and O < y_C: return "📈 상승 장악형"
    if lower_shadow > body * 2 and upper_shadow < body * 0.5 and body > 0: return "🔨 망치형 (매수세 유입)"
    if upper_shadow > body * 2 and lower_shadow < body * 0.5 and body > 0: return "☄️ 유성형 (매물 출회)"
    if body < total_range * 0.1: return "➕ 도지형 (눈치 보기)"
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

# 💡 신규 기능: 거래량 폭발 체크박스 기억 장치
if 'use_vol_surge' not in st.session_state: st.session_state.use_vol_surge = False

# 탭 2의 정밀 비교 결과를 저장할 '기억 공간'
if 'compare_results_df' not in st.session_state: st.session_state.compare_results_df = None
if 'backtest_results_df' not in st.session_state: st.session_state.backtest_results_df = None

# 💡 신규 기능: AI 채팅 꼬리 질문 기억 장치
if 'chat_history' not in st.session_state: st.session_state.chat_history = []
if 'current_target_code' not in st.session_state: st.session_state.current_target_code = None


# --- [사이드바: 종목 선별 기준] ---
st.sidebar.header("🔍 1~3단계: 전체 시장 스캔 필터")

with st.sidebar.expander("❓ 용어 및 분석 지표 설명"):
    st.caption("✅ **PER/PBR/ROE**: 가치평가의 기본 3요소")
    st.caption("📈 **MACD/RSI/볼린저/캔들**: 차트 바닥(반등) 타점 판독")
    st.caption("🦅 **52주 모멘텀**: 고점 돌파를 시도하는 강한 주도주 판독")

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
    # 💡 신규 기능 UI 추가: 거래량 폭발 체크박스
    st.session_state.use_vol_surge = st.checkbox("🔥 오늘 거래량 2배(200%) 폭발 종목만", value=st.session_state.use_vol_surge)

scan_button = st.sidebar.button("🎯 전체 시장 스캐너 가동 (최대 5분)", use_container_width=True)

st.sidebar.divider()
st.sidebar.header("⚡ 4단계: 관심종목 쾌속 스캔")
st.session_state.watchlist = st.sidebar.multiselect("장바구니 (검색하여 추가)", options=df_krx_full['Name'].tolist(), default=st.session_state.watchlist)
direct_scan_button = st.sidebar.button("🚀 선택 종목 다이렉트 분석 (1초)", type="primary", use_container_width=True)

# --- [메인 화면 로직: 매크로 풍향계 & 스캔] ---
st.title("📈 AI 심층 분석 시스템")

current_time_kst = (datetime.utcnow() + timedelta(hours=9)).strftime('%Y년 %m월 %d일 %H:%M:%S')

try:
    df_kospi = fdr.DataReader('KS11').tail(20)
    cur_kospi = df_kospi['Close'].iloc[-1]
    ma20_kospi = df_kospi['Close'].mean()
    if cur_kospi >= ma20_kospi:
        st.success(f"🧭 **오늘의 시장 풍향계:** 🟢 강세장 (코스피 20일선 돌파 유지 중) | 현재가: {cur_kospi:,.2f}pt\n\n💡 **AI 전략 조언:** 시장에 돈이 돌고 있습니다. RSI 70 이하의 정배열 우량주를 적극적으로 공략하세요!")
    else:
        st.error(f"🧭 **오늘의 시장 풍향계:** 🔴 약세장 (코스피 20일선 이탈) | 현재가: {cur_kospi:,.2f}pt\n\n💡 **AI 전략 조언:** 시장이 조정을 받고 있습니다. 가치평가(S-RIM) 대비 매우 저렴하고 하락방어율이 좋은 종목만 보수적으로 접근하세요.")
except:
    pass

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
        with st.spinner(f'1차: 시총 상위 {scan_limit}개 종목 로드 중... (최대 5분 소요)'):
            if excluded_sectors: df_krx = df_krx[~df_krx['Sector'].isin(excluded_sectors)]
            if st.session_state.use_marcap:
                min_marcap_won = st.session_state.min_marcap * 100000000
                filtered_by_cap = df_krx[df_krx['Marcap'] >= min_marcap_won].sort_values('Marcap', ascending=False).head(scan_limit)
            else:
                filtered_by_cap = df_krx.sort_values('Marcap', ascending=False).head(scan_limit)

    if not filtered_by_cap.empty:
        progress_text = f"2차: 네이버 금융 재무 데이터 수집 중..."
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
            progress_text2 = f"3차: 차트 분석 및 거래량 필터 진행 중..."
            progress_bar2 = st.progress(0, text=progress_text2)
            final_results = []
            total_survivors = len(survivors_df)
            survivors_records = survivors_df.to_dict('records')
            
            for idx, row_dict in enumerate(survivors_records):
                try:
                    df_price = fdr.DataReader(row_dict['Code']).tail(40) 
                    if not df_price.empty:
                        # 💡 신규 기능: 거래량 폭발 여부 확인 로직
                        if len(df_price) >= 20:
                            avg_vol_20 = df_price['Volume'].rolling(window=20).mean().iloc[-2]
                            cur_vol = df_price['Volume'].iloc[-1]
                            vol_ratio = (cur_vol / avg_vol_20) * 100 if avg_vol_20 > 0 else 0
                        else:
                            vol_ratio = 0
                        
                        # 거래량 급증 체크박스가 켜져있고, 비율이 200% 미만이면 탈락!
                        pass_vol_check = True
                        if st.session_state.use_vol_surge and vol_ratio < 200:
                            pass_vol_check = False
                        
                        if pass_vol_check:
                            rsi_val = calculate_rsi(df_price).iloc[-1]
                            if direct_scan_button or not st.session_state.use_rsi or rsi_val <= st.session_state.target_rsi:
                                row_dict['RSI'] = round(rsi_val, 1)
                                row_dict['거래량(%)'] = f"{int(vol_ratio)}%" # 표에 추가로 보여줍니다
                                final_results.append(row_dict)
                except: pass
                progress_bar2.progress((idx + 1) / total_survivors, text=f"{progress_text2} ({idx+1}/{total_survivors})")
            
            progress_bar2.empty()
            if final_results: st.session_state.scanned_data = pd.DataFrame(final_results).sort_values(by='ROE', ascending=False)
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
        display_df['시가총액(억)'] = display_df['시가총액(억)'].apply(lambda x: f"{x:,}")
        display_df['영업이익(억)'] = display_df['영업이익(억)'].apply(lambda x: f"{int(x):,}") 
        display_df['현재가'] = display_df['현재가'].apply(lambda x: f"{int(x):,}") 
        
        display_cols = ['Code', 'Name', '업종', '시가총액(억)', '현재가', 'PER', 'PBR', 'ROE', '부채비율(%)', '영업이익(억)']
        if 'RSI' in display_df.columns: display_cols.append('RSI')
        if '거래량(%)' in display_df.columns: display_cols.append('거래량(%)') # 💡 거래량 표시 추가
        st.dataframe(display_df[display_cols], use_container_width=True, hide_index=True)
        
        csv = convert_df_to_csv(display_df[display_cols])
        st.download_button(label="📥 엑셀(CSV)로 리스트 다운로드", data=csv, file_name='quant_watchlist.csv', mime='text/csv')
    
    # --- 탭 2: 정밀 분석 대시보드 (기존과 동일 생략 없이 유지) ---
    with tab2:
        st.info("💡 종목을 선택하여 볼린저밴드, 52주 신고가 모멘텀 등을 한눈에 비교하세요.")
        selected_names = st.multiselect("비교할 종목들을 선택하세요", final_df['Name'].tolist(), default=final_df['Name'].tolist()[:3])
        
        if st.button("🚀 선택 종목 정밀 비교", use_container_width=True) and selected_names:
            with st.spinner('차트 지표 및 모멘텀 데이터를 융합 분석 중입니다...'):
                compare_results = []
                backtest_results = []
                
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
                    
                    compare_results.append({
                        '종목명': name, '현재가': f"{int(current_price):,}원",
                        '① 이평선': cur_trend, '② MACD': cur_macd, 
                        '③ 방어율(눌림)': dd_signal, '④ 거래량': vol_sig, 
                        '⑤ 캔들': candle_sig, '⑥ 볼린저': bb_sig, '⑦ 모멘텀': momentum_sig
                    })
                    
                    periods = [("3개월 전", -60), ("6개월
