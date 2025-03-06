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

from extract_blog_content import blog_content


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

# 채널 정보 저장
def save_info(table_name, search_unique_id, keyword, channel_url, channel_name, channel_subscribers, 
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
    
    cur.execute(f"""
    INSERT INTO {table_name} (
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


# 채널 정보 불러오기
def get_info(search_id, table_name):  # channel_info
    conn = connect_postgres()
    cur = conn.cursor()

    cur.execute(f"""
    SELECT 
        channel_name, video_id, video_title, video_thumbnail, video_view_count, video_like_count, video_comment_count, video_view_subscriber_ratio, is_shorts, comment_1, comment_2, comment_3, transcript
    FROM 
        {table_name} 
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

# 키워드로 동영상 정보 불러오기
def fetch_youtube_data(search_query, max_results=50):
    youtube = build('youtube', 'v3', developerKey=YOUTUBE_API_KEY)
    
    videos_data = []
    request = youtube.search().list(
        q=search_query,
        part='snippet',
        type='video',
        maxResults=min(50, max_results)
    )
    
    response = request.execute()
    video_ids = [item['id']['videoId'] for item in response['items']]
    stats_request = youtube.videos().list(
        part='statistics',
        id=','.join(video_ids)
    )
    stats_response = stats_request.execute()
    channel_ids = [item['snippet']['channelId'] for item in response['items']]
    channels_request = youtube.channels().list(
        part='statistics',
        id=','.join(set(channel_ids))
    )
    channels_response = channels_request.execute()
    
    channel_subscribers = {
        channel['id']: int(channel['statistics'].get('subscriberCount', 0))
        for channel in channels_response['items']
    }
    
    for video, stats in zip(response['items'], stats_response['items']):
        views = int(stats['statistics'].get('viewCount', 0))
        if views >= 1000:
            video_id = video['id']['videoId']
            channel_id = video['snippet']['channelId']
            subscriber_count = channel_subscribers.get(channel_id, 0)
            view_sub_ratio = (views / subscriber_count) * 100 if subscriber_count > 0 else 0
            script = youtube_transcript(video_id)
            is_shorts = is_youtubeshorts(video_id)
            videos_data.append({
                'title': video['snippet']['title'], 
                'channel': video['snippet']['channelTitle'], 
                'publishedAt': video['snippet']['publishedAt'], 
                'views': views, 
                'subscribers': subscriber_count, 
                'view_sub_ratio': round(view_sub_ratio, 2),
                'likes': int(stats['statistics'].get('likeCount', 0)), 
                'comments': int(stats['statistics'].get('commentCount', 0)), 
                'description': video['snippet']['description'], 
                'url': f"https://www.youtube.com/watch?v={video_id}", 
                'thumbnail': video['snippet']['thumbnails']['high']['url'], 
                '1min_script': script,
                'is_shorts': is_shorts
            })
    
    return pd.DataFrame(videos_data)

# # 채널 동영상 분석 # #
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
        INSERT INTO channel_analysis (search_unique_id, video_id, video_title, is_shorts, llm_analysis) VALUES (%s, %s, %s, %s, %s)
        """, 
        (search_unique_id, video_id, video_title, is_shorts, analysis_result)
    )
    
    conn.commit()
    cur.close()
    conn.close()

# 각 search_unique_id별로 가장 높은 비율의 동영상 하나씩 가져오는 함수
def get_top_videos_by_search_id(table_name):
    conn = connect_postgres()
    cur = conn.cursor()

    # 모든 고유 search_unique_id 가져오기
    cur.execute(f"SELECT DISTINCT search_unique_id FROM {table_name} ORDER BY search_unique_id DESC")
    search_ids = [row[0] for row in cur.fetchall()]
    
    # 결과 데이터를 저장할 리스트
    results = []
    
    # 각 search_unique_id에 대해 가장 높은 video_view_subscriber_ratio를 가진 동영상 가져오기
    for search_id in search_ids:
        cur.execute(f"""
        SELECT 
            search_unique_id, keyword, channel_name, video_id, video_title, video_thumbnail, 
            video_view_count, video_like_count, video_comment_count, video_view_subscriber_ratio, 
            is_shorts, comment_1, comment_2, comment_3, transcript
        FROM 
            {table_name} 
        WHERE 
            search_unique_id = %s
        ORDER BY 
            video_view_subscriber_ratio DESC
        LIMIT 1
        """, (search_id,))
        
        row = cur.fetchone()
        if row:
            results.append(row)
    
    # 컬럼 이름
    columns = [
        'pk_ID', '키워드', '채널명', 'video_id', '제목', '썸네일', 
        '조회수', '좋아요', '댓글수', '조회수/구독자 비율', 
        '쇼츠', '댓글1', '댓글2', '댓글3', '스크립트'
    ]
    
    df = pd.DataFrame(results, columns=columns)
    
    cur.close()
    conn.close()
    
    return df

def blog_summarizer(client, text):
    try:
        # 입력 텍스트가 너무 길 경우 제한 (API 제한을 고려)
        if len(text) > 15000:
            text = text[:15000] + "..."
    
        summary = client.chat.completions.create(
            model='gpt-4o-2024-08-06', 
            messages=[
                {"role": "system", "content": "다음 블로그 포스트를 명확하고 간결하게 요약해주세요. 핵심 내용과 주요 포인트를 포함시켜야 합니다."},
                {"role": "user", "content": text}
            ],
            temperature=0.3, 
            max_tokens=500,
        )
        
        return summary.choices[0].message.content.strip()
    
    except Exception as e:
        return {"블로그 내용 요약 중 오류가 발생했습니다.": str(e)}


st.title("유튜브 채널 분석기")
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(["채널 데이터 수집", "키워드 기반 데이터 수집", "채널 데이터 조회", "키워드 데이터 조회", "블로그 분석기", "블로그 통합 분석"])

# 탭 1: 채널 데이터 수집 탭
with tab1:
    st.subheader("채널 데이터 불러오기")
    channel_url = st.text_input("유튜브 채널 주소 (e.g., https://youtube.com/@channelname)")
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
                    
                    is_shorts = is_youtubeshorts(video_id)  # 쇼츠 여부 확인
                    transcript = youtube_transcript(video_id)  # 자막 가져오기
                    view_subscriber_ratio = video['views'] / channel_stats['subscribers'] if channel_stats['subscribers'] > 0 else 0  # 조회수/구독자 비율 계산
                    comments = analyzer.get_top_comments(video_id, 3)  # 댓글 가져오기
                    
                    save_info(
                        'channel_info', pk_id, keyword, channel_url, channel_stats['title'], channel_stats['subscribers'], 
                        video_id, video['title'], video['thumbnail'], video['views'], video['like_count'], video['comment_count'], view_subscriber_ratio,
                        is_shorts, transcript, video['published_at'], comments
                    )
                    
                    progress_bar.progress((i + 1) / len(top_videos))  # 진행상황 업데이트
                
                st.success(f"성공적으로 채널 '{channel_stats['title']}'의 데이터를 저장했습니다!")

        except Exception as e:
            st.error(f"에러가 발생했습니다: {str(e)}")
    elif submit_button:
        st.warning("모든 필드를 입력해주세요: 채널 URL, 키워드, 검색 ID")

# 탭 2: 키워드 기반 데이터 수집 탭
with tab2:
    st.subheader("키워드로 유튜브 동영상 검색하기")
    
    query = st.text_input("분석하고 싶은 키워드를 입력하세요:")
    max_results = st.slider("분석할 영상 수", 10, 50, 30)
    search_button = st.button("검색 시작", type="primary")

    if query and search_button:
        with st.spinner("데이터를 수집하고 분석 중입니다..."):
            try:
                # 검색 고유 ID 생성
                pk_id = search_unique_id()

                df = fetch_youtube_data(query, max_results)
                
                st.subheader("📊 기본 통계")
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("총 조회수", f"{df['views'].sum():,}")
                with col2:
                    st.metric("평균 좋아요", f"{int(df['likes'].mean()):,}")
                with col3:
                    st.metric("평균 댓글", f"{int(df['comments'].mean()):,}")
                
                # 검색 결과를 데이터베이스에 저장
                progress_bar = st.progress(0)
                st.text("검색 결과를 데이터베이스에 저장 중...")
                
                # YouTube API 객체 생성 (댓글 정보를 가져오기 위함)
                analyzer = YouTubeAnalyzer(YOUTUBE_API_KEY)
                
                # 각 동영상 정보 처리 및 저장
                for i, (_, video) in enumerate(df.iterrows()):
                    video_id = video['url'].split('v=')[1] if 'v=' in video['url'] else video['url'].split('/')[-1]
                    
                    # 댓글 정보 가져오기
                    comments = analyzer.get_top_comments(video_id, 3)
                    
                    # channel_url 생성 (채널 이름으로부터)
                    channel_url = f"https://www.youtube.com/channel/{video_id}"
                    
                    # 데이터베이스에 저장
                    save_info(
                        'keyword_info', pk_id, query, channel_url, video['channel'], video['subscribers'],
                        video_id, video['title'], video['thumbnail'], video['views'], video['likes'], video['comments'], video['view_sub_ratio'],
                        video['is_shorts'], video['1min_script'], video['publishedAt'], comments
                    )
                    
                    # 진행 상황 업데이트
                    progress_bar.progress((i + 1) / len(df))
                
                st.success(f"성공적으로 키워드 '{query}'에 대한 {len(df)}개의 동영상 데이터를 저장했습니다! (검색 ID: {pk_id})")
                
                st.subheader("🔥 인기 영상 TOP 5")
                top_videos = df.nlargest(5, 'views')
                top_videos['views'] = top_videos['views'].apply(lambda x: format_to_10k(x) + " 건")
                top_videos['subscribers'] = top_videos['subscribers'].apply(lambda x: format_to_10k(x) + " 명")
                top_videos['view_sub_ratio'] = top_videos['view_sub_ratio'].apply(lambda x: f"{round(x)}%")
                top_videos['thumbnail'] = top_videos.apply(lambda x: f'<a href="{x["url"]}" target="_blank"><img src="{x["thumbnail"]}" width="240"/></a>', axis=1)
                display_videos = top_videos[['thumbnail', 'title', 'channel', 'views', 'subscribers', 'view_sub_ratio', 'is_shorts', '1min_script']]
                display_videos.columns = ['썸네일', '제목', '채널명', '조회수', '구독자수', '조회수/구독자 비율', '쇼츠', '최초 3분 스크립트']
                display_videos['쇼츠'] = display_videos['쇼츠'].map({True: '쇼츠', False: '롱폼'})
                st.markdown(display_videos.to_html(escape=False, index=False), unsafe_allow_html=True)
                
                df['engagement_rate'] = (df['likes'] + df['comments']) / df['views'] * 100
                st.subheader("📈 참여도 분석")
                col1, col2 = st.columns(2)
                with col1:
                    st.metric("평균 참여율", f"{df['engagement_rate'].mean():.2f}%")
                with col2:
                    st.metric("최고 참여율", f"{df['engagement_rate'].max():.2f}%")
            except Exception as e:
                st.error(f"오류가 발생했습니다: {str(e)}")

# 탭 3: 채널 데이터 조회 탭
with tab3:
    st.subheader("저장된 채널 데이터 조회")
    
    # 세션 상태 초기화
    if 'search_clicked_tab3' not in st.session_state:
        st.session_state.search_clicked_tab3 = False
    if 'shorts_analyzed_tab3' not in st.session_state:
        st.session_state.shorts_analyzed_tab3 = False
    if 'longform_analyzed_tab3' not in st.session_state:
        st.session_state.longform_analyzed_tab3 = False
    if 'shorts_analysis_result_tab3' not in st.session_state:
        st.session_state.shorts_analysis_result_tab3 = None
    if 'longform_analysis_result_tab3' not in st.session_state:
        st.session_state.longform_analysis_result_tab3 = None
    if 'found_data_tab3' not in st.session_state:
        st.session_state.found_data_tab3 = None
    
    # 먼저 모든 채널별 최고 성과 동영상 표시
    st.subheader("검색ID별 최고 성과 동영상 목록")
    
    try:
        top_videos_df = get_top_videos_by_search_id('channel_info')
        
        if not top_videos_df.empty:
            # 데이터 표시
            st.dataframe(
                top_videos_df,
                column_config={
                    "썸네일": st.column_config.ImageColumn(width="large", help="영상 썸네일"),
                    "검색ID": st.column_config.Column(width="small", help="이 ID를 아래 입력란에 입력하여 상세 분석"),
                    "키워드": st.column_config.Column(width="medium"),
                    "채널명": st.column_config.Column(width="medium"), 
                    "제목": st.column_config.Column(width="large"),
                    "조회수": st.column_config.Column(width="small"),
                    "좋아요": st.column_config.Column(width="small"),
                    "댓글수": st.column_config.Column(width="small"),
                    "조회수/구독자 비율": st.column_config.Column(width="small"),
                    "쇼츠": st.column_config.Column(width="small")
                },
                hide_index=True,
                use_container_width=True,
                height=300
            )
            
            # ID 선택에 도움이 되는 정보 추가
            st.info("👆 위 목록에서 상세 분석하고 싶은 검색ID를 확인하고, 아래에 입력하세요.")
        else:
            st.warning("저장된 채널 데이터가 없습니다.")
    except Exception as e:
        st.error(f"데이터 조회 중 오류가 발생했습니다: {str(e)}")
    
    # 구분선 추가
    st.markdown("---")
    
    # 특정 채널 상세 분석 섹션
    st.subheader("특정 검색ID 상세 분석")
    search_id_input = st.number_input("분석할 검색 ID를 입력하세요", min_value=1, step=1)
    
    # 검색 버튼 콜백
    def on_search_click_tab3():
        st.session_state.search_clicked_tab3 = True
        st.session_state.shorts_analyzed_tab3 = False
        st.session_state.longform_analyzed_tab3 = False
        st.session_state.shorts_analysis_result_tab3 = None
        st.session_state.longform_analysis_result_tab3 = None
        st.session_state.found_data_tab3 = None  # 새 검색 시 데이터 초기화
        
    # 쇼츠 분석 버튼 콜백
    def on_analyze_shorts_click_tab3():
        st.session_state.longform_analyzed_tab3 = True
    
    # 롱폼 분석 버튼 콜백
    def on_analyze_longform_click_tab3():
        st.session_state.longform_analyzed_tab3 = True
    
    search_button = st.button("검색", type="primary", key="search_button_tab3", on_click=on_search_click_tab3)
    
    # 검색 결과 표시
    if st.session_state.search_clicked_tab3:
        try:
            if 'found_data_tab3' not in st.session_state or st.session_state.found_data_tab3 is None:
                display_df = get_info(search_id_input, 'channel_info')
                st.session_state.found_data_tab3 = display_df
            else:
                display_df = st.session_state.found_data_tab3
            
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
                st.dataframe(
                    display_df,
                    column_config={
                        "썸네일": st.column_config.ImageColumn(width="large", help="영상 썸네일"),
                        "채널명": st.column_config.Column(width="medium"), 
                        "제목": st.column_config.Column(width="large"),
                        "조회수": st.column_config.Column(width="small"),
                        "좋아요": st.column_config.Column(width="small"),
                        "댓글수": st.column_config.Column(width="small"),
                        "조회수/구독자 비율": st.column_config.Column(width="small"),
                        "쇼츠": st.column_config.Column(width="small"), 
                        "댓글1": st.column_config.Column(width="large"), 
                        "댓글2": st.column_config.Column(width="large"), 
                        "댓글3": st.column_config.Column(width="large"), 
                        "스크립트": st.column_config.TextColumn(width="large")
                    },
                    hide_index=True,
                    use_container_width=True,
                    height=600
                )
                
                # 분석 섹션
                st.subheader("채널 데이터 분석하기")
                
                # 1. 쇼츠 분석 섹션
                st.write("### 쇼츠 영상 분석")
                
                if len(shorts_df) == 0:
                    st.info("해당 채널에는 쇼츠가 없습니다.")
                else:
                    # 쇼츠 분석 버튼
                    if not st.session_state.shorts_analyzed_tab3:
                        shorts_btn = st.button(
                            "쇼츠 분석 시작", 
                            type="primary", 
                            key="btn_analyze_shorts",
                            on_click=on_analyze_shorts_click_tab3
                        )
                    
                    # 분석 수행 및 결과 표시
                    if st.session_state.shorts_analyzed_tab3:
                        if st.session_state.shorts_analysis_result_tab3 is None:
                            with st.spinner("쇼츠 영상 분석 중..."):
                                # 쇼츠 분석 수행
                                shorts_analysis = analyze_video_data(openai_client, display_df, is_shorts=True)
                                st.session_state.shorts_analysis_result_tab3 = shorts_analysis
                                
                                # 분석 내용 저장
                                for _, row in shorts_df.iterrows():
                                    save_video_analysis(search_id_input, row['video_id'], row['제목'], True, shorts_analysis)
                                
                                st.success("쇼츠 영상 분석 완료 및 저장되었습니다!")
                        
                        # 저장된 분석 결과 표시
                        st.write(st.session_state.shorts_analysis_result_tab3)
                
                # 구분선
                st.markdown("---")
                
                # 2. 롱폼 분석 섹션
                st.write("### 롱폼 영상 분석")
                
                if len(longform_df) == 0:
                    st.info("해당 채널에는 롱폼이 없습니다.")
                else:
                    # 롱폼 분석 버튼
                    if not st.session_state.longform_analyzed_tab3:
                        longform_btn = st.button(
                            "롱폼 분석 시작", 
                            type="primary", 
                            key="btn_analyze_longform",
                            on_click=on_analyze_longform_click_tab3
                        )
                    
                    # 분석 수행 및 결과 표시
                    if st.session_state.longform_analyzed_tab3:
                        if st.session_state.longform_analysis_result_tab3 is None:
                            with st.spinner("롱폼 영상 분석 중..."):
                                # 롱폼 분석 수행
                                longform_analysis = analyze_video_data(openai_client, display_df, is_shorts=False)
                                st.session_state.longform_analysis_result_tab3 = longform_analysis
                                
                                # 분석 내용 저장
                                for _, row in longform_df.iterrows():
                                    save_video_analysis(search_id_input, row['video_id'], row['제목'], False, longform_analysis)
                                
                                st.success("롱폼 영상 분석 완료 및 저장되었습니다!")
                        
                        # 저장된 분석 결과 표시
                        st.write(st.session_state.longform_analysis_result_tab3)   
            else:
                st.warning(f"검색 ID {search_id_input}에 해당하는 데이터가 없습니다.")
                st.session_state.found_data_tab3 = None
        except Exception as e:
            st.error(f"데이터 조회 중 오류가 발생했습니다: {str(e)}")
            st.session_state.found_data_tab3 = None

# 탭 4: 키워드 데이터 조회 탭
with tab4:
    st.subheader("저장된 키워드 데이터 조회")
    
    # 세션 상태 초기화
    if 'search_clicked_tab4' not in st.session_state:
        st.session_state.search_clicked_tab4 = False
    if 'shorts_analyzed_tab4' not in st.session_state:  # 쇼츠 분석 완료 여부
        st.session_state.shorts_analyzed_tab4 = False
    if 'longform_analyzed_tab4' not in st.session_state:  # 롱폼 분석 완료 여부
        st.session_state.longform_analyzed_tab4 = False
    if 'shorts_analysis_result_tab4' not in st.session_state:  # 쇼츠 분석 결과
        st.session_state.shorts_analysis_result_tab4 = None
    if 'longform_analysis_result_tab4' not in st.session_state:  # 롱폼 분석 결과
        st.session_state.longform_analysis_result_tab4 = None
    if 'found_data_tab4' not in st.session_state:
        st.session_state.found_data_tab4 = None
    
    # 먼저 모든 채널별 최고 성과 동영상 표시
    st.subheader("검색ID별 최고 성과 동영상 목록")
    
    try:
        top_videos_df = get_top_videos_by_search_id('keyword_info')
        
        if not top_videos_df.empty:
            # 데이터 표시
            st.dataframe(
                top_videos_df,
                column_config={
                    "썸네일": st.column_config.ImageColumn(width="large", help="영상 썸네일"),
                    "검색ID": st.column_config.Column(width="small", help="이 ID를 아래 입력란에 입력하여 상세 분석"),
                    "키워드": st.column_config.Column(width="medium"),
                    "채널명": st.column_config.Column(width="medium"), 
                    "제목": st.column_config.Column(width="large"),
                    "조회수": st.column_config.Column(width="small"),
                    "좋아요": st.column_config.Column(width="small"),
                    "댓글수": st.column_config.Column(width="small"),
                    "조회수/구독자 비율": st.column_config.Column(width="small"),
                    "쇼츠": st.column_config.Column(width="small")
                },
                hide_index=True,
                use_container_width=True,
                height=300
            )
            
            # ID 선택에 도움이 되는 정보 추가
            st.info("👆 위 목록에서 상세 분석하고 싶은 검색ID를 확인하고, 아래에 입력하세요.")
        else:
            st.warning("저장된 채널 데이터가 없습니다.")
    except Exception as e:
        st.error(f"데이터 조회 중 오류가 발생했습니다: {str(e)}")
    
    # 구분선 추가
    st.markdown("---")
    
    # 특정 채널 상세 분석 섹션
    st.subheader("특정 검색ID 상세 분석")
    search_id_input = st.number_input("키워드에 대해 조회할 검색 ID를 입력하세요", min_value=1, step=1)
    
    # 검색 버튼 콜백
    def on_search_click_tab4():
        st.session_state.search_clicked_tab4 = True
        st.session_state.shorts_analyzed_tab4 = False
        st.session_state.longform_analyzed_tab4 = False
        st.session_state.shorts_analysis_result_tab4 = None
        st.session_state.longform_analysis_result_tab4 = None
        st.session_state.found_data_tab4 = None  # 새 검색 시 데이터 초기화
        
    # 쇼츠 분석 버튼 콜백
    def on_analyze_shorts_click_tab4():
        st.session_state.shorts_analyzed_tab4 = True
    
    # 롱폼 분석 버튼 콜백
    def on_analyze_longform_click_tab4():
        st.session_state.longform_analyzed_tab4 = True
    
    search_button_keyword = st.button("검색", type="primary", key="search_button_keyword_tab4", on_click=on_search_click_tab4)
    
    # 검색 결과 표시
    if st.session_state.search_clicked_tab4:
        try:
            if 'found_data_tab4' not in st.session_state or st.session_state.found_data_tab4 is None:
                display_df = get_info(search_id_input, 'keyword_info')
                st.session_state.found_data_tab4 = display_df
            else:
                display_df = st.session_state.found_data_tab4
            
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
                # st.dataframe(display_df, use_container_width=True)
                st.dataframe(
                    display_df,
                    column_config={
                        "썸네일": st.column_config.ImageColumn(width="large", help="영상 썸네일"),
                        "채널명": st.column_config.Column(width="medium"), 
                        "제목": st.column_config.Column(width="large"),
                        "조회수": st.column_config.Column(width="small"),
                        "좋아요": st.column_config.Column(width="small"),
                        "댓글 수": st.column_config.Column(width="small"),
                        "조회수/구독자 비율": st.column_config.Column(width="small"),
                        "쇼츠": st.column_config.Column(width="small"), 
                        "댓글1": st.column_config.Column(width="large"), 
                        "댓글2": st.column_config.Column(width="large"), 
                        "댓글3": st.column_config.Column(width="large"), 
                        "스크립트": st.column_config.TextColumn(width="large")
                    },
                    hide_index=True,
                    use_container_width=True,
                    height=600
                )
                
                # 분석 섹션
                st.subheader("채널 데이터 분석하기")
        
        except Exception as e:
            st.error(f"데이터 조회 중 오류가 발생했습니다: {str(e)}")
            st.session_state.found_data_tab4 = None

# 탭 5: 블로그 분석기 탭
with tab5:
    st.subheader("블로그 분석기")

    analysis_keyword = st.text_input('블로그 분석을 위한 키워드를 입력하세요. 분석 그룹의 이름을 결정합니다.')
    
    # # 블로그 한개 분석
    # blog_url = st.text_input('분석할 블로그 주소를 입력하세요.')  # 나중에 10개로 늘릴 예정
    # analyse_button = st.button("블로그 분석 시작", type="primary")
    
    # if blog_url and analysis_keyword and analyse_button:
    #     with st.spinner("블로그 내용을 분석 중입니다..."):
    #         try:
    #             extracted_data = blog_content(blog_url)

    #             blog_summary = blog_summarizer(openai_client, extracted_data['content'])

    #             conn = connect_postgres()
    #             cur = conn.cursor()

    #             # RETURNING 절 추가
    #             cur.execute("""
    #             INSERT INTO blog_summary (keyword, url, summary) 
    #             VALUES (%s, %s, %s) RETURNING id
    #             """, (analysis_keyword, blog_url, blog_summary))
                
    #             # 반환된 id 가져오기
    #             inserted_id = cur.fetchone()[0]

    #             conn.commit()
    #             cur.close()
    #             conn.close()

    #             st.subheader("블로그 요약 결과")
    #             st.write(blog_summary)
                
    #             st.success(f"블로그 분석 결과가 성공적으로 저장되었습니다! (ID: {inserted_id})")
    #         except Exception as e:
    #             st.error(f"블로그 분석 중 오류가 발생했습니다: {str(e)}")
    
    # # 블로그 여러개 분석
    blog_urls_container = st.container()  # 컨테이너 생성
    
    # 세션 상태에 URL 리스트 초기화
    if 'blog_urls' not in st.session_state:
        st.session_state.blog_urls = [""] * 10  # 10개의 빈 URL로 초기화
    
    # 5개씩 두 열로 나눠서 URL 입력 필드 생성
    col1, col2 = st.columns(2)
    
    # 왼쪽 컬럼 (0-4번 URL)
    with col1:
        for i in range(5):
            st.text_input(
                f"블로그 주소 {i+1}",
                value=st.session_state.blog_urls[i],
                key=f"blog_url_{i}"
            )
            # 입력 후 세션 상태 업데이트
            st.session_state.blog_urls[i] = st.session_state[f"blog_url_{i}"]
    
    # 오른쪽 컬럼 (5-9번 URL)
    with col2:
        for i in range(5, 10):
            st.text_input(
                f"블로그 주소 {i+1}",
                value=st.session_state.blog_urls[i],
                key=f"blog_url_{i}"
            )
            # 입력 후 세션 상태 업데이트
            st.session_state.blog_urls[i] = st.session_state[f"blog_url_{i}"]
    
    # 유효한 URL의 수 계산 (빈 문자열이 아닌 URL)
    valid_urls = [url for url in st.session_state.blog_urls if url.strip()]
    
    # 분석 버튼과 상태 표시
    analyse_button = st.button("블로그 분석 시작", type="primary", disabled=len(valid_urls) == 0 or not analysis_keyword)
    
    # 블로그 분석 실행
    if analysis_keyword and valid_urls and analyse_button:
        results_container = st.container()  # 분석 결과 저장용 컨테이너

        progress_bar = st.progress(0)  # 진행 상황 표시
        
        # 성공 및 실패 결과 수집
        success_count = 0
        failed_urls = []
        saved_ids = []
        
        # 각 URL 처리
        for i, url in enumerate(valid_urls):
            try:
                with st.spinner(f"블로그 분석 중... ({i+1}/{len(valid_urls)})"):
                    extracted_data = blog_content(url)  # 블로그 내용 추출
                    
                    blog_summary = blog_summarizer(openai_client, extracted_data['content'])  # 블로그 요약
                    
                    # DB에 저장
                    conn = connect_postgres()
                    cur = conn.cursor()
                    
                    cur.execute("""INSERT INTO blog_summary (keyword, url, summary) VALUES (%s, %s, %s) RETURNING id""", (analysis_keyword, url, blog_summary))
                    
                    inserted_id = cur.fetchone()[0]
                    saved_ids.append(inserted_id)
                    
                    conn.commit()
                    cur.close()
                    conn.close()
                    
                    # 성공 카운트 증가
                    success_count += 1
                
            except Exception as e:
                # 실패한 URL 기록
                failed_urls.append((url, str(e)))
                
                # 에러 발생 시 DB 연결 닫기
                try:
                    if 'conn' in locals() and conn:
                        conn.rollback()
                        cur.close()
                        conn.close()
                except:
                    pass
            
            # 진행 상황 업데이트
            progress_bar.progress((i + 1) / len(valid_urls))
        
        # 분석 완료 후 결과 표시
        with results_container:
            st.subheader("블로그 분석 결과")
            
            if success_count > 0:
                st.success(f"{success_count}개의 블로그가 성공적으로 분석되었습니다.")
                
                # 저장된 결과 가져오기
                conn = connect_postgres()
                cur = conn.cursor()
                
                # IN 구문을 위한 문자열 생성
                ids_str = ",".join(str(id) for id in saved_ids)
                
                if ids_str:
                    cur.execute(f"""SELECT id, url, summary FROM blog_summary WHERE id IN ({ids_str}) ORDER BY id""")
                    results = cur.fetchall()
                    
                    cur.close()
                    conn.close()
                    
                    # 결과 표시
                    for result in results:
                        with st.expander(f"블로그: {result[1]}"):
                            st.markdown(f"**ID**: {result[0]}")
                            st.markdown("**요약**:")
                            st.write(result[2])
            
            # 실패한 URL 표시
            if failed_urls:
                st.error(f"{len(failed_urls)}개의 블로그 분석에 실패했습니다.")
                for url, error in failed_urls:
                    with st.expander(f"실패한 URL: {url}"):
                        st.error(f"오류: {error}")
   
# 탭 6: 블로그 통합 분석 탭
with tab6:
    st.subheader("블로그 통합 분석")

    load_keyword = st.text_input('키워드를 입력하세요. 해당 키워드로 그루핑된 블로그 내용들을 통합 분석합니다.')
    int_analyse_button = st.button("통합 분석 시작", type="primary")  # integrated analysis
    
    if load_keyword and int_analyse_button:
        with st.spinner(f"키워드 '{load_keyword}'로 저장된 블로그 요약을 통합 분석 중..."):
            try:
                # 데이터베이스에서 해당 키워드로 저장된 요약 조회
                conn = connect_postgres()
                cur = conn.cursor()
                
                cur.execute("""
                SELECT summary FROM blog_summary 
                WHERE keyword = %s
                """, (load_keyword,))
                
                summaries = [row[0] for row in cur.fetchall()]
                
                # 조회된 요약이 없는 경우
                if not summaries:
                    st.error(f"키워드 '{load_keyword}'로 저장된 블로그 요약이 없습니다.")
                else:
                    # 통합 분석을 위한 프롬프트 작성
                    prompt = f"""
다음은 "{load_keyword}" 키워드와 관련된 {len(summaries)}개의 블로그 포스트 요약입니다:

"""
                    for i, summary in enumerate(summaries):
                        prompt += f"\n--- 블로그 {i+1} ---\n{summary}\n"
                    
                    prompt += f"""
위의 블로그 요약들을 통합적으로 분석하여, 다음 사항을 포함한 포괄적인 요약을 작성해주세요:

1. 주요 내용 및 공통 주제
2. 중요한 사실이나 정보
3. 서로 다른 관점이나 의견 (있는 경우)
4. 관련 이벤트나 일정 (있는 경우)
5. 전체 내용을 종합한 통찰

"{load_keyword}" 키워드를 중심으로 이 모든 블로그 내용을 잘 통합해서 요약해주세요.
"""
                    
                    # OpenAI API를 통한 통합 분석
                    response = openai_client.chat.completions.create(
                        model="gpt-4o-2024-08-06",
                        messages=[
                            {"role": "system", "content": "당신은 여러 블로그 포스트의 요약을 통합하여 분석하는 전문가입니다."},
                            {"role": "user", "content": prompt}
                        ],
                        temperature=0.3,
                        max_tokens=1500
                    )
                    
                    integrated_summary = response.choices[0].message.content.strip()
                    
                    # 고유 ID 생성
                    search_id = search_unique_id()
                    
                    # 통합 분석 결과 저장
                    cur.execute("""
                    INSERT INTO blog_int_summary (search_unique_id, keyword, int_summary)
                    VALUES (%s, %s, %s)
                    """, (search_id, load_keyword, integrated_summary))
                    
                    conn.commit()
                    
                    # 성공 메시지 표시
                    st.success(f"{len(summaries)}개의 블로그 요약이 성공적으로 통합 분석되었습니다.")
                    
                    # 결과 표시
                    st.subheader("통합 분석 결과")
                    st.write(integrated_summary)
                
                cur.close()
                conn.close()
                
            except Exception as e:
                st.error(f"통합 분석 중 오류가 발생했습니다: {str(e)}")
                # 에러 발생 시 연결 닫기
                try:
                    if 'conn' in locals() and conn:
                        conn.rollback()
                        cur.close()
                        conn.close()
                except:
                    pass
