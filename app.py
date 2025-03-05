import streamlit as st
import os
from dotenv import load_dotenv
import requests
import time
import pandas as pd

from googleapiclient.discovery import build
from youtube_transcript_api import YouTubeTranscriptApi

from openai import OpenAI

import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT


st.set_page_config(page_title="유튜브 채널 분석기", layout="wide")

load_dotenv()

YOUTUBE_API_KEY = os.getenv('YOUTUBE_API_KEY')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

openai_client = OpenAI(api_key=OPENAI_API_KEY)

# 유튜브 쇼츠인지 아닌지 구분
def is_youtubeshorts(video_id):
    url = 'https://www.youtube.com/shorts/' + video_id
    req = requests.head(url)
    
    return req.status_code == 200

# YouTube Transcript API로 스크립트 요약
def youtube_transcript(video_id, max_retries=3, retry_delay=1.5):
    """
    YouTube 동영상의 스크립트를 추출하는 함수.
    max_retries: 최대 재시도 횟수
    retry_delay: 재시도 사이의 대기 시간(초)
    """
    for retry in range(max_retries):
        try:
            if retry > 0:
                print(f"자막 추출 재시도 중... ({retry}/{max_retries-1})")
                time.sleep(retry_delay)  # 재시도 사이에 대기 시간 추가
            
            # 사용 가능한 자막 목록 확인
            transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
            
            # 디버깅을 위해 사용 가능한 모든 자막 출력
            print(f"\n영상 ID {video_id}의 사용 가능한 자막 목록:")
            for transcript in transcript_list:
                print(f"- {transcript.language} ({transcript.language_code}): {'자동 생성' if transcript.is_generated else '수동 생성'}")
            
            transcript = None
            
            # 순차적으로 자막 시도
            try:
                # 1. 수동 한국어 자막
                transcript = transcript_list.find_manually_created_transcript(['ko'])
                print('수동 생성 한국어 자막을 불러왔습니다.')
            except Exception as e1:
                try:
                    # 2. 자동 생성 한국어 자막
                    transcript = transcript_list.find_generated_transcript(['ko'])
                    print('자동 생성 한국어 자막을 불러왔습니다.')
                except Exception as e2:
                    try:
                        # 3. 다른 형식의 한국어 자막
                        for t in transcript_list:
                            if t.language_code.startswith('ko'):
                                transcript = t
                                print(f'한국어 자막을 찾았습니다: {t.language_code} ({"자동 생성" if t.is_generated else "수동 생성"})')
                                break
                    except Exception as e3:
                        print('한국어 자막을 찾을 수 없습니다.')
                        # 계속 진행하여 다른 시도를 해보기 위해 continue 하지 않음
            
            if transcript:
                try:
                    # 자막 텍스트 추출
                    transcript_data = transcript.fetch()
                    first_minute = []
                    current_time = 0
                    
                    for line in transcript_data:
                        if current_time > 180:  # 180초 = 3분
                            break
                        first_minute.append(line['text'])
                        current_time += line['duration']
                    
                    result = ' '.join(first_minute)
                    if result:
                        print(f'성공적으로 {current_time:.1f}초 분량의 자막을 추출했습니다.')
                        return result
                    else:
                        print('자막은 찾았으나 내용이 비어있습니다.')
                except Exception as e:
                    print(f'자막 추출 중 오류 발생: {str(e)}')
            else:
                print('사용 가능한 자막을 찾지 못했습니다.')
                
        except Exception as e:
            print(f'스크립트를 불러오는 중 오류가 발생했습니다: {str(e)}')
        
        # 재시도가 남아 있으면 계속 시도
        if retry < max_retries - 1:
            continue
    
    # 모든 시도가 실패하면 명확한 메시지 반환
    return "⚠️ 자막을 가져올 수 없습니다 (여러 번 시도했으나 실패)"

# 만 단위로 변환
def format_to_10k(n):
    num = round(n / 10000, 1)  # 소수점 첫째자리에서 반올림
    return f"{num}만"


# PostgreSQL에 접속
def connect_postgres():
    conn = psycopg2.connect(
        host="15.164.112.237", 
        database="dify", 
        user="difyuser", 
        password="bico0218"
    )
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    
    return conn

def search_unique_id():
    conn = connect_postgres()
    cur = conn.cursor()
    
    # 시퀀스가 없으면 생성
    cur.execute("CREATE SEQUENCE IF NOT EXISTS youtube_search_seq")
    
    # 다음 시퀀스 값 가져오기
    cur.execute("SELECT nextval('youtube_search_seq')")
    search_id = cur.fetchone()[0]
    
    cur.close()
    conn.close()
    
    return search_id

def save_channel_info(search_unique_id, keyword, channel_url, channel_name, channel_subscribers, 
                      video_id, video_title, video_thumbnail, video_view_count, video_like_count, video_comment_count, video_view_subscriber_ratio, 
                      is_shorts, transcript, published_at, top_comments):
    conn = connect_postgres()
    cur = conn.cursor()
    
    # 댓글 정보 준비 (각 댓글을 별도 변수로)
    comment_1 = ""
    comment_2 = ""
    comment_3 = ""
    
    # 댓글 정보 준비 (내용만 저장)
    comment_1 = comments[0]['text'] if len(comments) > 0 else "내용 없음"
    comment_2 = comments[1]['text'] if len(comments) > 1 else "내용 없음"
    comment_3 = comments[2]['text'] if len(comments) > 2 else "내용 없음"
    
    cur.execute("""
    INSERT INTO channel_info (
        search_unique_id, keyword, channel_url, channel_name, channel_subscribers, 
        video_id, video_title, video_thumbnail, video_view_count, video_like_count, video_comment_count, video_view_subscriber_ratio, 
        is_shorts, transcript, published_at, comment_1, comment_2, comment_3)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, 
        (search_unique_id, keyword, channel_url, channel_name, channel_subscribers, 
         video_id, video_title, video_thumbnail, video_view_count, video_like_count, video_comment_count, video_view_subscriber_ratio, 
         is_shorts, transcript, published_at, comment_1, comment_2, comment_3)
    )
    
    conn.commit()
    cur.close()
    conn.close()

# 유튜브 채널 데이터 수집
class YouTubeAnalyzer:
    def __init__(self, api_key):
        self.youtube = build('youtube', 'v3', developerKey=api_key)
    
    # Extract channel ID from URL or custom URL
    def get_channel_id(self, channel_url):
        if 'youtube.com/channel/' in channel_url:
            return channel_url.split('channel/')[1].split('/')[0]
        elif 'youtube.com/@' in channel_url:
            username = channel_url.split('@')[1].split('/')[0]
            request = self.youtube.channels().list(
                part='id',
                forUsername=username
            )
            try:
                response = request.execute()
                if response.get('items'):
                    return response['items'][0]['id']
            except:
                pass
            
            # If the above method fails, try with search
            request = self.youtube.search().list(
                part='snippet',
                q=username,
                type='channel',
                maxResults=1
            )
            response = request.execute()
            
            # Verify the channel handle matches
            for item in response['items']:
                channel_id = item['snippet']['channelId']
                channel_info = self.youtube.channels().list(
                    part='snippet',
                    id=channel_id
                ).execute()
                
                if channel_info['items'][0]['snippet'].get('customUrl', '').lower() == f'@{username.lower()}':
                    return channel_id
            
            raise ValueError(f"Could not find channel ID for {channel_url}")
    
    # Get channel statistics including subscriber count
    def get_channel_stats(self, channel_id):
        request = self.youtube.channels().list(
            part='statistics,snippet',
            id=channel_id
        )
        response = request.execute()
        channel_info = response['items'][0]
        return {
            'title': channel_info['snippet']['title'],
            'subscribers': int(channel_info['statistics']['subscriberCount']),
            'thumbnail': channel_info['snippet']['thumbnails']['high']['url']
        }
    
    # 해당 채널의 동영상 정보
    def get_all_videos(self, channel_id):
        videos = []
        next_page_token = None
        
        while True:
            request = self.youtube.search().list(
                part='snippet',
                channelId=channel_id,
                maxResults=50,
                type='video',
                pageToken=next_page_token,
                order='date'  # 최신 영상부터
            )
            response = request.execute()
            
            # Get video IDs for batch statistics request
            video_ids = [item['id']['videoId'] for item in response['items']]
            
            if not video_ids:
                break
            
            # Get video statistics in batch
            stats_request = self.youtube.videos().list(
                part='statistics',
                id=','.join(video_ids)
            )
            stats_response = stats_request.execute()
            
            # Combine video information with statistics
            for video, stats in zip(response['items'], stats_response['items']):
                videos.append({
                    'title': video['snippet']['title'],
                    'thumbnail': video['snippet']['thumbnails']['high']['url'],
                    'views': int(stats['statistics'].get('viewCount', 0)),
                    'like_count': int(stats['statistics'].get('likeCount', 0)),
                    'comment_count': int(stats['statistics'].get('commentCount', 0)),
                    'published_at': video['snippet']['publishedAt'],
                    'video_id': video['id']['videoId']
                })
            
            next_page_token = response.get('nextPageToken')
            if not next_page_token:
                break
        
        return videos
    
    # 상위 n개 댓글
    def get_top_comments(self, video_id, max_results=3):
        """Get top comments for a specific video"""
        try:
            request = self.youtube.commentThreads().list(
                part='snippet',
                videoId=video_id,
                maxResults=max_results,
                order='relevance'  # 인기순 정렬
            )
            response = request.execute()
            
            comments = []
            for item in response.get('items', []):
                comment = item['snippet']['topLevelComment']['snippet']
                comments.append({
                    'author': comment['authorDisplayName'],
                    'text': comment['textDisplay'],
                    'like_count': comment['likeCount'],
                    'published_at': comment['publishedAt']
                })
            
            return comments
        except Exception as e:
            # 댓글이 비활성화된 경우 등의 예외 처리
            return [{'author': '댓글 없음', 'text': '댓글을 가져올 수 없습니다 (비활성화되었거나 접근 불가)', 'like_count': 0, 'published_at': ''}]


def get_channel_info(search_id):
    conn = connect_postgres()
    cur = conn.cursor()

    cur.execute("""
    SELECT 
        channel_name, video_id, video_title, video_thumbnail, video_view_count, video_like_count, video_comment_count, video_view_subscriber_ratio, is_shorts, comment_1, comment_2, comment_3, transcript
    FROM 
        channel_info 
    WHERE 
        search_unique_id = %s
    """, (search_id,))

    results = cur.fetchall()

    # 키워드도 조회해야
    columns = [
        '채널명', 'video_id', '제목', '썸네일', '조회수', '좋아요', '댓글수', 
        '조회수/구독자 비율', '쇼츠', '댓글1', '댓글2', '댓글3', '스크립트'
    ]
    
    df = pd.DataFrame(results, columns=columns)
    
    cur.close()
    conn.close()
    
    return df


# # 동영상 분석 # #
# 영상 데이터 분석 함수
def analyze_video_data(client, videos_data, is_shorts=False):
    """
    Parameters:
    client (OpenAI): OpenAI 클라이언트
    videos_data (DataFrame): 분석할 영상 데이터
    is_shorts (bool): 쇼츠 영상 분석 여부 (True: 쇼츠, False: 롱폼)
    
    Returns:
    str: 분석 결과
    """
    # 해당 카테고리(쇼츠/롱폼)에 맞는 영상 필터링
    if is_shorts:
        filtered_data = videos_data[videos_data['쇼츠'] == True]
        content_type = "쇼츠(Shorts)"
    else:
        filtered_data = videos_data[videos_data['쇼츠'] == False]
        content_type = "롱폼(Longform)"
    
    # 데이터가 없는 경우
    if filtered_data.empty:
        return f"분석할 {content_type} 영상이 없습니다."
    
    # 데이터 준비
    data_summary = {
        "영상_수": len(filtered_data),
        "평균_조회수": filtered_data['조회수'].mean(),
        "평균_좋아요": filtered_data['좋아요'].mean(),
        "평균_댓글수": filtered_data['댓글수'].mean(),
        "평균_조회구독비율": filtered_data['조회수/구독자 비율'].mean(),
        "최고_조회수": filtered_data['조회수'].max(),
        "최고_좋아요": filtered_data['좋아요'].max(),
        "최고_댓글수": filtered_data['댓글수'].max()
    }
    
    # 상위 3개 영상 정보 (조회수 기준)
    top_videos = filtered_data.sort_values(by='조회수', ascending=False).head(3)
    top_videos_info = []
    
    for _, row in top_videos.iterrows():
        video_info = {
            "제목": row['제목'],
            "조회수": row['조회수'],
            "좋아요": row['좋아요'],
            "댓글수": row['댓글수'],
            "조회구독비율": row['조회수/구독자 비율']
        }
        top_videos_info.append(video_info)
    
    # 프롬프트 작성
    channel_name = filtered_data['채널명'].iloc[0]
    
    prompt = f"""
    다음은 YouTube 채널 "{channel_name}"의 {content_type} 영상 {len(filtered_data)}개에 대한 데이터입니다:
    
    전체 데이터 요약:
    - 영상 수: {data_summary['영상_수']}개
    - 평균 조회수: {data_summary['평균_조회수']:.1f}회
    - 평균 좋아요: {data_summary['평균_좋아요']:.1f}개
    - 평균 댓글 수: {data_summary['평균_댓글수']:.1f}개
    - 평균 조회수/구독자 비율: {data_summary['평균_조회구독비율']:.4f}
    
    상위 3개 영상 (조회수 기준):
    """
    
    for i, video in enumerate(top_videos_info):
        prompt += f"""
    {i+1}. "{video['제목']}"
       - 조회수: {video['조회수']}회
       - 좋아요: {video['좋아요']}개
       - 댓글 수: {video['댓글수']}개
       - 조회수/구독자 비율: {video['조회구독비율']:.4f}
        """
    
    prompt += f"""
    위 데이터를 바탕으로 다음 내용을 분석해주세요:
    1. 조회수, 좋아요, 댓글 수의 전반적인 트렌드와 상관관계
    2. 가장 인기 있는 영상들의 공통점 (제목 패턴, 시청자 참여도 등)
    3. 시청자 참여도가 높은 영상의 특징 (좋아요/조회수 비율, 댓글/조회수 비율 등)
    4. 조회수/구독자 비율을 통해 본 영상의 인기도 분석
    5. {content_type} 영상의 성공 요인과 개선점 제안
    
    분석 결과는 400-500단어 내외로 작성해주세요.
    """
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o-2024-08-06",
            messages=[
                {"role": "system", "content": "당신은 유튜브 채널과 영상 데이터를 분석하는 전문가입니다. 데이터를 깊이 있게 분석하고 통찰력 있는 인사이트를 제공해주세요."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=1000
        )
        
        # 응답에서 텍스트 추출
        return response.choices[0].message.content.strip()
        
    except Exception as e:
        return f"데이터 분석 중 오류가 발생했습니다: {str(e)}"

# 분석 결과 저장 함수
def save_video_analysis(search_unique_id, video_id, video_title, is_shorts, analysis_result):
    conn = connect_postgres()
    cur = conn.cursor()
    
    cur.execute("""
        INSERT INTO channel_analysis (search_unique_id, video_id, video_title, is_shorts, llm_analysis) VALUES (%s, %s, %s, %s, %s)""", 
        (search_unique_id, video_id, video_title, is_shorts, analysis_result)
    )
    
    conn.commit()
    cur.close()
    conn.close()

st.title("유튜브 채널 분석기")

tab1, tab2 = st.tabs(["데이터 수집", "데이터 조회"])

# 데이터 수집 탭
with tab1:
    channel_url = st.text_input("YouTube Channel URL (e.g., https://youtube.com/@channelname)")
    keyword = st.text_input("동영상 제작에 사용할 키워드를 입력하세요")

    submit_button = st.button("데이터 수집 및 저장", type="primary")

    if submit_button and channel_url and keyword:
        try:
            with st.spinner("채널 정보를 가져오는 중..."):
                analyzer = YouTubeAnalyzer(YOUTUBE_API_KEY)
                
                # 고유 검색 ID 생성 (PostgreSQL에서 자동으로 생성)
                pk_id = search_unique_id()
                
                # 채널 ID 가져오기
                channel_id = analyzer.get_channel_id(channel_url)
                
                # 채널 통계 가져오기
                channel_stats = analyzer.get_channel_stats(channel_id)
                
                # 채널 정보 출력
                st.subheader(f"채널: {channel_stats['title']}")
                col1, col2 = st.columns([1, 3])
                with col1:
                    st.image(channel_stats['thumbnail'], width=150)
                with col2:
                    st.write(f"구독자: {format_to_10k(channel_stats['subscribers'])} ({channel_stats['subscribers']}명)")
                
                # 동영상 정보 가져오기
                videos = analyzer.get_all_videos(channel_id)
                
                # 상위 10개 동영상만 처리 (또는 전체 동영상이 10개 미만인 경우)
                top_videos = videos[:10]
                
                st.subheader(f"상위 {len(top_videos)}개 동영상 분석 및 저장 중...")
                progress_bar = st.progress(0)
                
                # 각 동영상 정보 처리 및 저장
                for i, video in enumerate(top_videos):
                    video_id = video['video_id']
                    
                    # 쇼츠 여부 확인
                    is_shorts = is_youtubeshorts(video_id)
                    
                    # 자막 가져오기
                    transcript = youtube_transcript(video_id)
                    
                    # 조회수/구독자 비율 계산
                    view_subscriber_ratio = video['views'] / channel_stats['subscribers'] if channel_stats['subscribers'] > 0 else 0
                    
                    # 댓글 가져오기
                    comments = analyzer.get_top_comments(video_id, 3)
                    
                    save_channel_info(
                        pk_id, keyword, channel_url, channel_stats['title'], channel_stats['subscribers'], 
                        video_id, video['title'], video['thumbnail'], video['views'], video['like_count'], video['comment_count'], view_subscriber_ratio,
                        is_shorts, transcript, video['published_at'], comments
                    )
                    
                    # 진행상황 업데이트
                    progress_bar.progress((i + 1) / len(top_videos))
                
                st.success(f"성공적으로 채널 '{channel_stats['title']}'의 데이터를 저장했습니다!")

        except Exception as e:
            st.error(f"에러가 발생했습니다: {str(e)}")
    elif submit_button:
        st.warning("모든 필드를 입력해주세요: 채널 URL, 키워드, 검색 ID")

# 탭 2: 데이터 조회 탭
with tab2:
    st.subheader("저장된 데이터 조회")
    
    search_id_input = st.number_input("조회할 검색 ID를 입력하세요", min_value=1, step=1)
    
    # 세션 상태 초기화
    if 'search_clicked' not in st.session_state:
        st.session_state.search_clicked = False
    if 'shorts_analyzed' not in st.session_state:  # 쇼츠 분석 완료 여부
        st.session_state.shorts_analyzed = False
    if 'longform_analyzed' not in st.session_state:  # 롱폼 분석 완료 여부
        st.session_state.longform_analyzed = False
    if 'shorts_analysis_result' not in st.session_state:  # 쇼츠 분석 결과
        st.session_state.shorts_analysis_result = None
    if 'longform_analysis_result' not in st.session_state:  # 롱폼 분석 결과
        st.session_state.longform_analysis_result = None
    if 'found_data' not in st.session_state:
        st.session_state.found_data = None
    
    # 검색 버튼 콜백
    def on_search_click():
        st.session_state.search_clicked = True
        st.session_state.shorts_analyzed = False
        st.session_state.longform_analyzed = False
        st.session_state.shorts_analysis_result = None
        st.session_state.longform_analysis_result = None
        st.session_state.found_data = None  # 새 검색 시 데이터 초기화
        
    # 쇼츠 분석 버튼 콜백
    def on_analyze_shorts_click():
        st.session_state.shorts_analyzed = True
    
    # 롱폼 분석 버튼 콜백
    def on_analyze_longform_click():
        st.session_state.longform_analyzed = True
    
    search_button = st.button("검색", type="primary", key="search_button", on_click=on_search_click)
    
    # 검색 결과 표시
    if st.session_state.search_clicked:
        try:
            if 'found_data' not in st.session_state or st.session_state.found_data is None:
                display_df = get_channel_info(search_id_input)
                st.session_state.found_data = display_df
            else:
                display_df = st.session_state.found_data
            
            if not display_df.empty:
                st.success(f"검색 ID {search_id_input}에 해당하는 데이터를 찾았습니다.")
                
                # 쇼츠와 롱폼 영상 분리해서 통계 표시
                shorts_df = display_df[display_df['쇼츠'] == True]
                longform_df = display_df[display_df['쇼츠'] == False]
                
                col1, col2 = st.columns(2)
                with col1:
                    st.subheader("쇼츠 영상")
                    st.write(f"영상 수: {len(shorts_df)}")
                    if not shorts_df.empty:
                        st.write(f"평균 조회수: {shorts_df['조회수'].mean():.1f}")
                        st.write(f"평균 좋아요: {shorts_df['좋아요'].mean():.1f}")
                
                with col2:
                    st.subheader("롱폼 영상")
                    st.write(f"영상 수: {len(longform_df)}")
                    if not longform_df.empty:
                        st.write(f"평균 조회수: {longform_df['조회수'].mean():.1f}")
                        st.write(f"평균 좋아요: {longform_df['좋아요'].mean():.1f}")
                
                # 전체 데이터 표시
                st.subheader("모든 영상 데이터")
                st.dataframe(display_df, use_container_width=True)
                
                # 분석 섹션
                st.subheader("채널 데이터 분석하기")
                
                # 1. 쇼츠 분석 섹션
                st.write("### 쇼츠 영상 분석")
                
                if len(shorts_df) == 0:
                    st.info("해당 채널에는 쇼츠가 없습니다.")
                else:
                    # 쇼츠 분석 버튼
                    if not st.session_state.shorts_analyzed:
                        shorts_btn = st.button(
                            "쇼츠 분석 시작", 
                            type="primary", 
                            key="btn_analyze_shorts",
                            on_click=on_analyze_shorts_click
                        )
                    
                    # 분석 수행 및 결과 표시
                    if st.session_state.shorts_analyzed:
                        if st.session_state.shorts_analysis_result is None:
                            with st.spinner("쇼츠 영상 분석 중..."):
                                # 쇼츠 분석 수행
                                shorts_analysis = analyze_video_data(openai_client, display_df, is_shorts=True)
                                st.session_state.shorts_analysis_result = shorts_analysis
                                
                                # 분석 내용 저장
                                for _, row in shorts_df.iterrows():
                                    save_video_analysis(
                                        search_id_input,
                                        row['video_id'],
                                        row['제목'],
                                        True,
                                        shorts_analysis
                                    )
                                
                                st.success("쇼츠 영상 분석 완료 및 저장되었습니다!")
                        
                        # 저장된 분석 결과 표시
                        st.write(st.session_state.shorts_analysis_result)
                
                # 구분선
                st.markdown("---")
                
                # 2. 롱폼 분석 섹션
                st.write("### 롱폼 영상 분석")
                
                if len(longform_df) == 0:
                    st.info("해당 채널에는 롱폼이 없습니다.")
                else:
                    # 롱폼 분석 버튼
                    if not st.session_state.longform_analyzed:
                        longform_btn = st.button(
                            "롱폼 분석 시작", 
                            type="primary", 
                            key="btn_analyze_longform",
                            on_click=on_analyze_longform_click
                        )
                    
                    # 분석 수행 및 결과 표시
                    if st.session_state.longform_analyzed:
                        if st.session_state.longform_analysis_result is None:
                            with st.spinner("롱폼 영상 분석 중..."):
                                # 롱폼 분석 수행
                                longform_analysis = analyze_video_data(openai_client, display_df, is_shorts=False)
                                st.session_state.longform_analysis_result = longform_analysis
                                
                                # 분석 내용 저장
                                for _, row in longform_df.iterrows():
                                    save_video_analysis(search_id_input, row['video_id'], row['제목'], False, longform_analysis)
                                
                                st.success("롱폼 영상 분석 완료 및 저장되었습니다!")
                        
                        # 저장된 분석 결과 표시
                        st.write(st.session_state.longform_analysis_result)
                
            else:
                st.warning(f"검색 ID {search_id_input}에 해당하는 데이터가 없습니다.")
                st.session_state.found_data = None
        except Exception as e:
            st.error(f"데이터 조회 중 오류가 발생했습니다: {str(e)}")
            st.session_state.found_data = None
    
