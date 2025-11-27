import os
import requests
from flask import Flask, render_template, request, jsonify
import math
# 引入 Groq 套件
from groq import Groq
# 引入 urllib3 來關閉 SSL 警告
import urllib3

# 禁用 SSL 警告 (因為我們要忽略氣象局的憑證錯誤)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)

# ==========================================
# 👇 設定 API Keys 👇
# 1. 氣象局 API Key
# 建議在 Render 的 Environment Variables 設定，這裡提供預設值僅供測試
CWA_API_KEY = os.environ.get("CWA_API_KEY", "CWA-E9D51C81-8614-4973-AC00-B6714CBD6AF4")

# 2. Groq API Key
# 建議在 Render 的 Environment Variables 設定
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "請填入你的_GROQ_API_KEY")
# ==========================================

# 初始化 Groq 客戶端
if not GROQ_API_KEY or "請填入" in GROQ_API_KEY:
    print("⚠️ 警告: 你尚未填入 GROQ_API_KEY，AI 功能將無法運作！")
    client = None
else:
    client = Groq(api_key=GROQ_API_KEY)

# --- 0. 提供模型列表 ---
@app.route('/models')
def get_models():
    return jsonify([
        "llama-3.3-70b-versatile",
        "llama-3.1-8b-instant",
        "gemma2-9b-it",
        "mixtral-8x7b-32768"
    ])

# --- 1. 抓取降雨機率 (用縣市預報 F-C0032-001) ---
def get_rain_chance(county_name):
    url = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-C0032-001"
    params = {"Authorization": CWA_API_KEY, "format": "JSON", "locationName": county_name}
    
    try:
        # verify=False: 忽略 SSL 憑證驗證，解決 Render 連不上氣象局的問題
        response = requests.get(url, params=params, verify=False)
        data = response.json()
        if "records" in data and "location" in data['records']:
            all_locs = data['records']['location']
            for loc in all_locs:
                if loc['locationName'] == county_name:
                    weather_elements = loc['weatherElement']
                    pop = next((x for x in weather_elements if x['elementName'] == 'PoP'), None)
                    if pop: return int(pop['time'][0]['parameter']['parameterName'])
    except Exception as e:
        print(f"DEBUG: 抓取降雨機率失敗: {e}")
    return 0

# --- 2. 抓取精準天氣 (加入「同縣市救援」機制) ---
def get_weather_data(user_input):
    # 1. 處理輸入
    raw_input = user_input.strip().replace('台', '臺')
    short_input = raw_input
    
    counties = ["臺北市","新北市","桃園市","臺中市","臺南市","高雄市","基隆市","新竹市","嘉義市","新竹縣","苗栗縣","彰化縣","南投縣","雲林縣","嘉義縣","屏東縣","宜蘭縣","花蓮縣","臺東縣","澎湖縣","金門縣","連江縣"]
    
    county_hint = "" 
    for c in counties:
        if c in raw_input:
            county_hint = c
            short_input = raw_input.replace(c, "")
            break
            
    if short_input == "": short_input = raw_input

    # API 設定
    url = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/O-A0003-001"
    params = {"Authorization": CWA_API_KEY, "format": "JSON", "StationStatus": "OPEN"}

    try:
        # 👇 除錯訊息：檢查 Key 是否正確讀取
        print(f"DEBUG: 正在向氣象局請求資料... (Key前幾碼: {CWA_API_KEY[:5]}...)") 
        
        # verify=False: 忽略 SSL 憑證驗證
        response = requests.get(url, params=params, verify=False)
        
        # 👇 除錯訊息：檢查連線狀態
        if response.status_code != 200:
            print(f"❌ 氣象局 API 失敗！狀態碼: {response.status_code}")
            print(f"❌ 錯誤訊息: {response.text}")
            return None
            
        data = response.json()
        print("✅ 氣象局 API 連線成功，開始搜尋測站...")
        
        best_station = None   
        backup_station = None 
        
        for station in data['records']['Station']:
            st_name = station['StationName']
            st_town = station['GeoInfo']['TownName']
            st_county = station['GeoInfo']['CountyName']
            
            # 策略 A: 收集「備用測站」
            if county_hint and st_county == county_hint:
                if backup_station is None: backup_station = station
            
            # 策略 B: 尋找「完美測站」
            if county_hint and county_hint not in st_county: continue 

            if short_input in st_name or short_input in st_town or st_name in short_input:
                best_station = station
                if short_input == st_town or short_input == st_name: break
        
        # --- 最終決定使用哪個測站 ---
        final_station = best_station if best_station else backup_station
        
        # 👇 除錯訊息：檢查是否有找到測站
        if not final_station:
            print(f"❌ 搜尋失敗：在列表中找不到符合 '{short_input}' 或 '{county_hint}' 的測站")
            return None
        
        if final_station:
            w = final_station['WeatherElement']
            geo = final_station['GeoInfo']
            
            temp = float(w['AirTemperature'])
            if temp < -50: temp = 25 
            humid = float(w['RelativeHumidity'])
            wind_mps = float(w['WindSpeed'])
            desc = w['Weather']

            feels_like = temp + 0.33*(humid/100)*6.105*math.exp((17.27*temp)/(237.7+temp)) - 0.7*wind_mps - 4.0
            
            wind_level = 0
            if wind_mps >= 0.3: wind_level = 1
            if wind_mps >= 1.6: wind_level = 2
            if wind_mps >= 3.4: wind_level = 3
            if wind_mps >= 5.5: wind_level = 4
            if wind_mps >= 8.0: wind_level = 5
            if wind_mps >= 10.8: wind_level = 6
            
            rain_prob = get_rain_chance(geo['CountyName'])

            display_city = f"{geo['CountyName']} {geo['TownName']}"
            if best_station is None and backup_station:
                    display_city = f"{geo['CountyName']} (鄰近測站: {geo['TownName']})"

            return {
                "city": display_city,
                "temp": round(temp, 1),
                "feels_like": round(feels_like, 1),
                "humidity": int(humid),
                "wind_speed": wind_level,
                "description": desc,
                "rain_chance": rain_prob
            }
        return None

    except Exception as e:
        print(f"❌ 程式發生嚴重錯誤: {e}")
        return None

# --- 3. AI 建議 (Groq SDK) ---
def get_ai_recommendation(weather, model_name):
    if not client:
        return "⚠️ AI 功能未啟用，請確認已設定 GROQ_API_KEY 環境變數。"

    if not model_name or model_name == "llama3.2": 
        model_name = "llama-3.1-8b-instant"

    prompt = f"""
    你是一位貼心專業的穿搭顧問。
    數據:
    地點: {weather['city']}
    氣溫: {weather['temp']} (體感 {weather['feels_like']})
    降雨機率: {weather['rain_chance']}%
    風力: {weather['wind_speed']}級
    天氣狀況: {weather['description']}
    
    請用繁體中文給一段80字建議。降雨機率高要帶傘。體感低要防風。語氣要非常親切，像朋友一樣。
    """
    
    try:
        chat_completion = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model=model_name,
        )
        return chat_completion.choices[0].message.content
    except Exception as e:
        print(f"Groq Error: {e}")
        return "AI 連線忙碌中，請檢查 API Key 是否正確。"

@app.route('/')
def index(): return render_template('index.html')

@app.route('/recommend', methods=['POST'])
def recommend():
    city = request.form.get('city')
    model = request.form.get('model')
    
    if not city: return jsonify({"error": "請輸入城市名稱"})
    
    weather = get_weather_data(city)
    if not weather: 
        return jsonify({"error": f"找不到 '{city}'，請確認輸入正確的縣市名稱。"})
    
    ai_advice = get_ai_recommendation(weather, model)
    return jsonify({"weather": weather, "advice": ai_advice})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
