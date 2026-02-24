from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from youtube_transcript_api import YouTubeTranscriptApi
import google.generativeai as genai
import os
import re
import json

app = FastAPI()

# 프론트엔드(React)에서 서버에 접속할 수 있도록 CORS 허용 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # 실제 배포 시에는 프론트엔드 URL만 넣는 것이 좋습니다.
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 1. 환경 변수에서 Gemini API 키 가져오기 (Replit Secrets에 설정 필요)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
# 최신 무료 모델 사용
model = genai.GenerativeModel('gemini-2.5-flash')

def extract_video_id(url: str):
    """유튜브 URL에서 Video ID만 추출하는 함수"""
    match = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11}).*", url)
    if match:
        return match.group(1)
    return None

@app.get("/")
def read_root():
    return {"status": "Server is running!"}

@app.get("/api/analyze")
def analyze_youtube_video(video_url: str):
    video_id = extract_video_id(video_url)
    if not video_id:
        raise HTTPException(status_code=400, detail="Invalid YouTube URL")

    try:
        # 2. 유튜브 자막 추출 (영어 자막 우선)
        transcript = YouTubeTranscriptApi.get_transcript(video_id, languages=['en'])
        
        # 자막 텍스트만 하나로 뭉치기 (AI에게 보낼 용도)
        full_text = " ".join([t['text'] for t in transcript])
        
        # 3. Gemini AI에게 프롬프트(명령) 전달하여 분석 요청
        prompt = f"""
        다음은 유튜브 영상의 전체 영어 스크립트입니다. 
        이 스크립트를 문맥에 맞게 3~5문장 단위로 나누어 자연스러운 한국어로 번역하고, 
        스크립트에 등장하는 중요 영단어(비즈니스, IT, 경제 관련 위주) 5개를 뽑아 뜻을 정리해주세요.
        
        반드시 아래의 JSON 형식으로만 응답해주세요:
        {{
            "script": [
                {{"text": "영어 원문 문장들...", "translation": "한국어 번역..."}},
                {{"text": "다음 영어 원문 문장들...", "translation": "한국어 번역..."}}
            ],
            "vocab": [
                {{"word": "단어1", "meaning": "뜻1"}},
                {{"word": "단어2", "meaning": "뜻2"}}
            ]
        }}
        
        스크립트 전문: {full_text[:5000]} # 토큰 제한 방지를 위해 일단 5000자만 보냅니다.
        """
        
        response = model.generate_content(prompt)
        
        # AI 응답 텍스트에서 JSON 부분만 추출
        response_text = response.text
        # 마크다운 백틱 제거 등 클리닝 과정
        if "```json" in response_text:
            response_text = response_text.split("```json")[1].split("```")[0].strip()
        elif "```" in response_text:
            response_text = response_text.split("```")[1].strip()
            
        ai_result = json.loads(response_text)
        
        # 타임라인 데이터(start)를 AI 결과에 억지로 끼워맞추는 간단한 로직 (프로토타입용)
        # 실제 서비스에서는 AI에게 타임스탬프 매칭까지 요구해야 하나, 여기서는 비율로 나눔
        chunk_size = len(transcript) // len(ai_result['script'])
        for i, item in enumerate(ai_result['script']):
            try:
                # 대략적인 시작 시간 매칭
                item['start'] = transcript[i * chunk_size]['start']
                item['id'] = i + 1
                item['speaker'] = "Speaker"
            except IndexError:
                item['start'] = 0
                item['id'] = i + 1
                item['speaker'] = "Speaker"

        return ai_result

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing video: {str(e)}")
