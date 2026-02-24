from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from youtube_transcript_api import YouTubeTranscriptApi
import google.generativeai as genai
import os
import re
import json
import traceback

app = FastAPI()

# CORS settings to allow communication with Vercel frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Gemini AI with API Key from environment variables
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
else:
    print("WARNING: GEMINI_API_KEY is not set in Render environment variables.")

# Use the stable gemini-1.5-flash model
model = genai.GenerativeModel('gemini-1.5-flash')

def extract_video_id(url: str):
    """Extracts the 11-character video ID from a YouTube URL."""
    match = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11}).*", url)
    return match.group(1) if match else None

@app.get("/")
def health_check():
    """Health check endpoint to verify server status."""
    return {"status": "ok", "message": "Server is running"}

@app.get("/api/analyze")
def analyze_youtube_video(video_url: str):
    print(f"\n--- Starting analysis for: {video_url} ---")
    video_id = extract_video_id(video_url)
    
    if not video_id:
        print("Error: Invalid YouTube URL")
        raise HTTPException(status_code=400, detail="올바르지 않은 유튜브 URL입니다.")

    # 1. Fetch Transcript (자막 추출 단계 - 모든 언어 지원으로 강화)
    try:
        print(f"Attempting to fetch transcript for video: {video_id}")
        
        if hasattr(YouTubeTranscriptApi, 'list_transcripts'):
            transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
            try:
                # 1순위: 사람이 직접 만든 영어 자막
                transcript = transcript_list.find_manually_created_transcript(['en'])
                print("Found manual English transcript.")
            except:
                try:
                    # 2순위: 자동 생성된 영어 자막
                    transcript = transcript_list.find_generated_transcript(['en'])
                    print("Found auto-generated English transcript.")
                except:
                    # 3순위: 영어 자막이 아예 없는 경우, 아무 언어나 가져와서 영어로 자동 번역!
                    available_transcripts = list(transcript_list)
                    if not available_transcripts:
                        raise Exception("영상에 어떠한 자막도 존재하지 않습니다.")
                    
                    # 첫 번째로 발견된 자막(예: 한국어)을 영어로 번역
                    transcript = available_transcripts[0].translate('en')
                    print(f"Translated {available_transcripts[0].language} transcript to English.")
            
            data = transcript.fetch()
        else:
            print("WARNING: Render 서버의 캐시로 인해 구버전 라이브러리를 사용하여 자막을 추출합니다.")
            data = YouTubeTranscriptApi.get_transcript(video_id, languages=['en'])

        full_text = " ".join([t['text'] for t in data])
        print(f"Successfully fetched transcript. Length: {len(full_text)} chars.")
        
    except Exception as e:
        error_trace = traceback.format_exc()
        print(f"Transcript Fetch Error:\n{error_trace}")
        raise HTTPException(status_code=400, detail=f"[자막 추출 실패] 자막을 가져올 수 없습니다. 상세 오류: {str(e)}")

    # 2. AI Processing with Gemini (AI 분석 단계)
    try:
        print("Sending request to Gemini AI...")
        prompt = f"""
        당신은 전문 번역가이자 언어 학습 가이드입니다.
        제공된 유튜브 영어 스크립트를 문맥에 따라 3~5문장씩 의미 단위로 나누어 번역해주세요.
        또한 학습하기 좋은 주요 단어 5개를 선정해주세요.
        
        반드시 아래 JSON 형식으로만 응답하세요:
        {{
            "script": [
                {{"text": "English sentences...", "translation": "한국어 번역..."}}
            ],
            "vocab": [
                {{"word": "단어", "meaning": "뜻"}}
            ]
        }}
        
        스크립트: {full_text[:7000]}
        """
        response = model.generate_content(prompt)
        response_text = response.text
        print("Received response from Gemini AI.")
        
    except Exception as e:
        error_trace = traceback.format_exc()
        print(f"Gemini API Error:\n{error_trace}")
        raise HTTPException(status_code=500, detail=f"[AI 분석 실패] Gemini API 통신 중 오류가 발생했습니다. API 키나 제공량을 확인해주세요. 상세 오류: {str(e)}")

    # 3. Parse JSON & Align Timestamps (결과 처리 단계)
    try:
        print("Parsing JSON response...")
        if "```json" in response_text:
            response_text = response_text.split("```json")[1].split("```")[0].strip()
        elif "```" in response_text:
            response_text = response_text.split("```")[1].strip()
            
        ai_result = json.loads(response_text)
        
        chunk_size = max(1, len(data) // len(ai_result.get('script', [])))
        for i, item in enumerate(ai_result.get('script', [])):
            idx = min(i * chunk_size, len(data) - 1)
            item['start'] = data[idx]['start']
            item['id'] = i + 1
            item['speaker'] = "Speaker"

        print("Analysis successful. Returning data.")
        return ai_result
        
    except json.JSONDecodeError as e:
        print(f"JSON Parsing Error: AI returned invalid JSON format. Response text: {response_text}")
        raise HTTPException(status_code=500, detail="[결과 처리 실패] AI가 올바른 형식(JSON)으로 응답하지 않았습니다.")
    except Exception as e:
        error_trace = traceback.format_exc()
        print(f"Data Processing Error:\n{error_trace}")
        raise HTTPException(status_code=500, detail=f"[데이터 병합 실패] 분석 결과를 처리하는 중 오류가 발생했습니다. 상세 오류: {str(e)}")
