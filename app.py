import streamlit as st
import pandas as pd
import os
from dotenv import load_dotenv

from googleapiclient.discovery import build

from openai import OpenAI

import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

# 커스텀 모듈
from youtube import is_youtubeshorts, youtube_transcript
from saveNload import save_info, load_info, fetch_youtube_data, get_top_videos_by_search_id, save_video_analysis, save_video_analysis_keyword, save_thumbnail_analysis
from blog import blog_content, blog_summarizer
from analyse_video import analyze_channel_video, analyze_keyword_video, analyze_thumbnails
from feedback import save_feedback_yt, save_feedback_ig, save_feedback_th


st.set_page_config(page_title="유튜브 채널 분석기", layout="wide")

load_dotenv()

YOUTUBE_API_KEY = os.getenv('YOUTUBE_API_KEY')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

openai_client = OpenAI(api_key=OPENAI_API_KEY)

llm_option = st.selectbox("LLM 선택", ('gpt-4o-2024-08-06', 'gpt-4o-mini-2024-07-18', 'gpt-3.5-turbo-0125'))  # 'o1-mini-2024-09-12'


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

# 만 단위로 변환
def format_to_10k(n):
    num = round(n / 10000, 1)  # 소수점 첫째자리에서 반올림
    return f"{num}만"

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


# # 메인 탭 # #
st.title("유튜브 트렌드 분석기")
tab_channel, tab_keyword, tab_blog, tab_result, tab_content = st.tabs(["채널 분석", "키워드 분석", "블로그 요약", "썸네일 분석 내용 정리", "컨텐츠 생성 및 평가"])

# 채널 데이터 탭
with tab_channel:
    if 'channel_status' not in st.session_state:
        st.session_state.channel_status = 'initial'  # 'initial', 'confirmed', 'show_existing'
    if 'current_channel' not in st.session_state:
        st.session_state.current_channel = None
    if 'current_channel_keyword' not in st.session_state:
        st.session_state.current_channel_keyword = None
    
    st.subheader("채널 분석")
    channel_url = st.text_input("유튜브 채널 주소 (e.g., https://youtube.com/@channelname)")
    keyword = st.text_input("동영상 제작에 사용할 키워드를 입력하세요")
    st.info("👆 입력한 키워드에 대한 유튜브 콘텐츠를 제작할 때 채널 정보를 참고하여 만드는데 쓰입니다.")
    submit_button = st.button("채널 데이터 수집 시작", type="primary")

    if submit_button and channel_url and keyword:
        st.session_state.current_channel = channel_url
        st.session_state.current_channel_keyword = keyword
        
        try:
            conn = connect_postgres()
            cur = conn.cursor()

            cur.execute("""SELECT COUNT(*) FROM info_channel WHERE channel_url = %s """, (channel_url,))
            count = cur.fetchone()[0]
            
            cur.close()
            conn.close()

            if count > 0:
                st.session_state.channel_status = 'confirm_needed'
            else:
                st.session_state.channel_status = 'confirmed'
            
            st.rerun()
        
        except Exception as e:
            st.error(f"에러가 발생했습니다: {str(e)}")
    
    elif submit_button:
        st.warning("채널 URL, 키워드를 모두 입력해주세요")
            
    # 
    if st.session_state.channel_status == 'confirm_needed':
        st.warning("이미 분석된 적 있는 채널입니다. 그래도 분석을 진행하시겠습니까?")
        
        col1, col2 = st.columns(2)
        with col1:
            if st.button("예", key="confirm_channel_yes"):
                st.session_state.channel_status = 'confirmed'
                st.rerun()
        with col2:
            if st.button("아니오", key="confirm_channel_no"):
                st.session_state.channel_status = 'show_existing'
                st.rerun()
    
    # 기존 분석 결과 표시
    if st.session_state.channel_status == 'show_existing':
        try:
            conn = connect_postgres()
            cur = conn.cursor()
            
            cur.execute("""
            SELECT search_unique_id, keyword, channel_name, video_title, video_view_subscriber_ratio, is_shorts
            FROM info_channel 
            WHERE channel_url = %s 
            ORDER BY search_unique_id DESC
            """, (st.session_state.current_channel,))
            
            existing_results = cur.fetchall()
            
            cur.close()
            conn.close()
            
            if existing_results:
                st.subheader(f"키워드 '{st.session_state.current_channel_keyword}'의 기존 분석 결과")

                df = pd.DataFrame(
                    existing_results, 
                    columns=["ID", "키워드", "채널명", "영상 제목", "조회수/구독자 비율", "쇼츠"]
                )
                st.dataframe(
                    df,
                    column_config={
                        "ID": st.column_config.Column(width="small"),
                        "키워드": st.column_config.Column(width="medium"),
                        "채널명": st.column_config.Column(width="medium"),
                        "영상 제목": st.column_config.Column(width="large"),
                        "조회수/구독자 비율": st.column_config.Column(width="medium"),
                        "쇼츠": st.column_config.Column(width="small")
                    },
                    hide_index=True,
                    use_container_width=True
                )
            else:
                st.info(f"해당 채널에 대한 결과를 찾을 수 없습니다.")
            
            # 상태 재설정을 위한 버튼
            if st.button("새 분석 시작", key='new_analysis_channel'):
                st.session_state.keyword_status = 'initial'
                st.session_state.current_channel = None
                st.session_state.current_channel_keyword = None
                st.rerun()
                
        except Exception as e:
            st.error(f"결과 조회 중 오류가 발생했습니다: {str(e)}")

    # 채널 분석 진행 
    if st.session_state.channel_status == 'confirmed' and st.session_state.current_channel:
        with st.spinner("채널 정보를 가져오는 중..."):
            try:
                analyzer = YouTubeAnalyzer(YOUTUBE_API_KEY)
                
                # 고유 검색 ID 생성 (PostgreSQL에서 자동으로 생성)
                pk_id = search_unique_id()
                
                # 채널 정보 가져오기
                channel_url = st.session_state.current_channel
                keyword = st.session_state.current_channel_keyword
                
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
                        'info_channel', pk_id, keyword, channel_url, channel_stats['title'], channel_stats['subscribers'], 
                        video_id, video['title'], video['thumbnail'], video['views'], video['like_count'], video['comment_count'], view_subscriber_ratio,
                        is_shorts, transcript, video['published_at'], comments
                    )
                    
                    progress_bar.progress((i + 1) / len(top_videos))  # 진행상황 업데이트
                
                st.success(f"성공적으로 채널 '{channel_stats['title']}'의 데이터를 저장했습니다!")
        
                # 분석 완료 후 상태 초기화
                st.session_state.channel_status = 'initial'
            
            except Exception as e:
                st.error(f"채널 분석 중 오류가 발생했습니다: {str(e)}")
                st.session_state.channel_status = 'initial'

    st.markdown("---")

    st.subheader("저장된 채널 데이터 조회")
    
    # 세션 상태 초기화
    if 'search_clicked_channel' not in st.session_state:
        st.session_state.search_clicked_channel = False
    if 'shorts_analyzed_channel' not in st.session_state:
        st.session_state.shorts_analyzed_channel = False
    if 'longform_analyzed_channel' not in st.session_state:
        st.session_state.longform_analyzed_channel = False
    if 'shorts_analysis_result_channel' not in st.session_state:
        st.session_state.shorts_analysis_result_channel = None
    if 'longform_analysis_result_channel' not in st.session_state:
        st.session_state.longform_analysis_result_channel = None
    if 'shorts_thumbnail_analysis_channel' not in st.session_state:  # 추가
        st.session_state.shorts_thumbnail_analysis_channel = None
    if 'longform_thumbnail_analysis_channel' not in st.session_state:  # 추가
        st.session_state.longform_thumbnail_analysis_channel = None
    if 'found_data_channel' not in st.session_state:
        st.session_state.found_data_channel = None
    
    try:
        top_videos_df = get_top_videos_by_search_id('info_channel')
        
        if not top_videos_df.empty:
            search_keyword = st.text_input("조회할 키워드를 입력하세요.", key='search_keyword_ch')
            search_channel = st.text_input("조회할 채널명을 입력하세요.", key='search_channel_ch')
            
            filtered_df = top_videos_df.copy()
            
            # 키워드 필터링
            if search_keyword:
                filtered_df = filtered_df[filtered_df['키워드'].str.contains(search_keyword, case=False, na=False)]
            
            # 채널명 필터링
            if search_channel:
                filtered_df = filtered_df[filtered_df['채널명'].str.contains(search_channel, case=False, na=False)]

            # 버튼 동작을 위한 콜백 함수
            def analyze_channel(search_id):
                st.session_state.search_clicked_channel = True
                st.session_state.shorts_analyzed_channel = False
                st.session_state.longform_analyzed_channel = False
                st.session_state.shorts_analysis_result_channel = None
                st.session_state.longform_analysis_result_channel = None
                st.session_state.shorts_thumbnail_analysis_channel = None
                st.session_state.longform_thumbnail_analysis_channel = None
                st.session_state.found_data_channel = None
                st.session_state.selected_search_id = search_id  # 선택한 ID 저장
                
                # 데이터 로드
                display_df = load_info(search_id, 'info_channel')
                st.session_state.found_data_channel = display_df
            
            # 버튼 열과 데이터프레임을 나란히 배치
            col_buttons, col_table = st.columns([1, 7])
            
            with col_buttons:
                for _, row in filtered_df.iterrows():
                    search_id = row['pk_ID']
                    if st.button(f"📊 ID {search_id} 분석", key=f"analyze_btn_{search_id}", help=f"ID {search_id} 분석"):
                        analyze_channel(search_id)
            
            # # 각 행에 분석 버튼 추가를 위한 버튼 열 생성
            # top_videos_df['분석'] = top_videos_df['pk_ID'].apply(
            #     lambda x: f'<button key="analyze_{x}">분석</button>'
            # )
            
            with col_table:
                # 데이터프레임 표시 (버튼 열 포함)
                st.dataframe(
                    filtered_df,
                    column_config={
                        "썸네일": st.column_config.ImageColumn(width="large", help="영상 썸네일"),
                        "pk_ID": st.column_config.Column(width="small", help="채널 ID"),
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
                    height=300, 
                )
            
            # 도움말 메시지 수정
            st.info("👆 위 목록에서 분석하고 싶은 채널의 '분석' 버튼을 클릭하세요.")
        else:
            st.warning("저장된 채널 데이터가 없습니다.")
    except Exception as e:
        st.error(f"데이터 조회 중 오류가 발생했습니다: {str(e)}")
    
    # # 특정 채널 상세 분석 섹션
    # st.subheader("특정 검색ID 상세 분석")
    # search_id_input = st.number_input("채널에 대해 분석할 pk_ID를 입력하세요", min_value=1, step=1)
    
    # 쇼츠 분석 버튼 콜백
    def on_analyze_shorts_click_channel():
        st.session_state.shorts_analyzed_channel = True
    
    # 롱폼 분석 버튼 콜백
    def on_analyze_longform_click_channel():
        st.session_state.longform_analyzed_channel = True
    
    # search_button = st.button("분석 시작", type="primary", key="search_button_tab3", on_click=analyze_channel)
    
    if 'selected_search_id' in st.session_state:
        st.subheader(f"선택한 채널 ID {st.session_state.selected_search_id} 분석 결과")

    # 검색 결과 표시
    if st.session_state.search_clicked_channel:
        try:
            if 'found_data_channel' not in st.session_state or st.session_state.found_data_channel is None:
                display_df = load_info(st.session_state.selected_search_id, 'info_channel')  # display_df = load_info(search_id_input, 'info_channel')
                st.session_state.found_data_channel = display_df
            else:
                display_df = st.session_state.found_data_channel
            
            if not display_df.empty:
                st.success(f"검색 ID {st.session_state.selected_search_id}에 해당하는 데이터를 찾았습니다.")  # st.success(f"검색 ID {search_id_input}에 해당하는 데이터를 찾았습니다.")
                
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
                
                # 분석 섹션
                st.subheader("채널 데이터 분석하기")

                col_shorts, col_long = st.columns(2)
                
                with col_shorts:
                    # 1. 쇼츠 분석 섹션
                    if 'shorts_analysis_status' not in st.session_state:
                        st.session_state.shorts_analysis_status = 'initial'  # 'initial', 'confirm_needed', 'confirmed', 'show_existing'
                    
                    st.write("### 쇼츠 영상 분석")
                    
                    if len(shorts_df) == 0:
                        st.info("해당 채널에는 쇼츠가 없습니다.")
                    else:
                        # 쇼츠 분석 버튼
                        if not st.session_state.shorts_analyzed_channel:
                            shorts_btn = st.button(
                                "쇼츠 분석 시작", 
                                type="primary", 
                                key="btn_analyze_shorts_channel",
                                # on_click=on_analyze_shorts_click_channel
                            )

                            if shorts_btn:
                                try:
                                    conn = connect_postgres()
                                    cur = conn.cursor()

                                    cur.execute("""
                                    SELECT COUNT(*) FROM analysis_channel
                                    WHERE search_unique_id = %s AND is_shorts = TRUE
                                    """, (st.session_state.selected_search_id,))
                                    
                                    count = cur.fetchone()[0]
                                    
                                    cur.close()
                                    conn.close()
                                    
                                    if count > 0:
                                        st.session_state.shorts_analysis_status = 'confirm_needed'
                                    else:
                                        st.session_state.shorts_analysis_status = 'confirmed'
                                        on_analyze_shorts_click_channel()
                                    
                                    st.rerun()

                                except Exception as e:
                                    st.error(f"분석 결과 확인 중 오류가 발생했습니다: {str(e)}")
                        
                        # 해당 채널 쇼츠가 이미 분석된 적 있는 경우
                        if st.session_state.shorts_analysis_status == 'confirm_needed':
                            st.warning("이 채널의 쇼츠 영상은 이미 분석된 적이 있습니다. 그래도 다시 분석을 진행하시겠습니까?")

                            col1, col2 = st.columns(2)
                            with col1:
                                if st.button("예", key="confirm_shorts_yes_ch"):
                                    st.session_state.shorts_analysis_status = 'confirmed'
                                    on_analyze_shorts_click_channel()  # 콜백 함수 호출
                                    st.rerun()
                            with col2:
                                if st.button("아니오", key="confirm_shorts_no_ch"):
                                    st.session_state.shorts_analysis_status = 'show_existing'
                                    st.rerun()
                        
                        if st.session_state.shorts_analysis_status == 'show_existing':
                            try:
                                conn = connect_postgres()
                                cur = conn.cursor()
                                
                                # 이전 분석 결과 가져오기
                                cur.execute("""
                                SELECT search_unique_id, channel_result, created_at 
                                FROM analysis_channel 
                                WHERE search_unique_id = %s AND is_shorts = TRUE
                                ORDER BY created_at DESC
                                """, (st.session_state.selected_search_id,))
                                
                                existing_results = cur.fetchall()
                                
                                cur.close()
                                conn.close()
                                
                                if existing_results:
                                    st.subheader("기존 쇼츠 분석 결과")
                                    
                                    for i, (search_id, analysis_result, created_at) in enumerate(existing_results):
                                        with st.expander(f"분석 결과 #{i+1} (생성일: {created_at})", expanded=(i==0)):
                                            st.markdown(analysis_result)
                                    
                                    # # 썸네일 분석 결과도 가져오기
                                    # try:
                                    #     conn = connect_postgres()
                                    #     cur = conn.cursor()
                                        
                                    #     cur.execute("""
                                    #     SELECT thumbnail_url, thumbnail_analysis 
                                    #     FROM analysis_thumbnail 
                                    #     WHERE search_unique_id = %s AND is_shorts = TRUE
                                    #     ORDER BY created_at DESC LIMIT 3
                                    #     """, (st.session_state.selected_search_id,))
                                        
                                    #     thumbnail_results = cur.fetchall()
                                        
                                    #     cur.close()
                                    #     conn.close()
                                        
                                    #     if thumbnail_results:
                                    #         st.write("### 인기 썸네일 분석")
                                    #         for i, (thumbnail_url, thumbnail_analysis) in enumerate(thumbnail_results):
                                    #             cols = st.columns([1, 2])
                                    #             with cols[0]:
                                    #                 st.image(thumbnail_url)
                                    #             with cols[1]:
                                    #                 st.markdown(thumbnail_analysis)
                                    # except Exception as e:
                                    #     st.error(f"썸네일 분석 결과 조회 중 오류가 발생했습니다: {str(e)}")
                                else:
                                    st.info("이 채널에 대한 기존 쇼츠 분석 결과를 찾을 수 없습니다.")
                                
                                # 새 분석 시작 버튼
                                if st.button("새 분석 시작", key="new_shorts_analysis_ch"):
                                    st.session_state.shorts_analysis_status = 'initial'
                                    st.rerun()
                                    
                            except Exception as e:
                                st.error(f"기존 분석 결과 조회 중 오류가 발생했습니다: {str(e)}")

                        # 분석 수행 및 결과 표시
                        if st.session_state.shorts_analyzed_channel:
                            if st.session_state.shorts_analysis_result_channel is None:
                                with st.spinner("쇼츠 영상 분석 중..."):
                                    # 쇼츠 분석 수행
                                    shorts_analysis = analyze_channel_video(openai_client, llm_option, display_df, is_shorts=True)
                                    st.session_state.shorts_analysis_result_channel = shorts_analysis

                                    thumbnail_analysis_shorts = analyze_thumbnails(openai_client, display_df, is_shorts=True)
                                    st.session_state.shorts_thumbnail_analysis_channel = thumbnail_analysis_shorts
                                    
                                    # 분석 내용 저장
                                    save_video_analysis('analysis_channel', st.session_state.selected_search_id, True, shorts_analysis)  # save_video_analysis('channel_analysis', search_id_input, True, shorts_analysis)
                                    save_thumbnail_analysis(thumbnail_analysis_shorts, st.session_state.selected_search_id, True, display_df['채널URL'].iloc[0])  # save_thumbnail_analysis(thumbnail_analysis_shorts, search_id_input, True, display_df['채널URL'].iloc[0])
                                    
                                    st.success("쇼츠 영상 분석 완료 및 저장되었습니다!")

                                    # 분석 완료 후 상태 초기화
                                    st.session_state.shorts_analysis_status = 'initial'
                            
                            # 저장된 분석 결과 표시
                            st.write(st.session_state.shorts_analysis_result_channel)

                            # 썸네일 분석 결과 표시
                            st.write("### 인기 썸네일 분석")
                            if isinstance(st.session_state.shorts_thumbnail_analysis_channel, list):
                                for analysis in st.session_state.shorts_thumbnail_analysis_channel:
                                    st.write(f"#### {analysis['제목']}")
                                    cols = st.columns([1, 2])
                                    with cols[0]:
                                        st.image(analysis['썸네일'])
                                    with cols[1]:
                                        st.write(analysis['분석'])
                            else:
                                st.write(st.session_state.shorts_thumbnail_analysis_channel)
                
                # # 구분선
                # st.markdown("---")
                
                with col_long:
                    # 2. 롱폼 분석 섹션
                    if 'longform_analysis_status' not in st.session_state:
                        st.session_state.longform_analysis_status = 'initial'  # 'initial', 'confirm_needed', 'confirmed', 'show_existing'
                    
                    st.write("### 롱폼 영상 분석")
                    
                    if len(longform_df) == 0:
                        st.info("해당 채널에는 롱폼이 없습니다.")
                    else:
                        # 롱폼 분석 버튼
                        if not st.session_state.longform_analyzed_channel:
                            longform_btn = st.button(
                                "롱폼 분석 시작", 
                                type="primary", 
                                key="btn_analyze_longform",
                                # on_click=on_analyze_longform_click_channel
                            )

                            if longform_btn:
                                try:
                                    conn = connect_postgres()
                                    cur = conn.cursor()
                    
                                    cur.execute("""
                                    SELECT COUNT(*) FROM analysis_channel 
                                    WHERE search_unique_id = %s AND is_shorts = FALSE
                                    """, (st.session_state.selected_search_id,))
                                    
                                    count = cur.fetchone()[0]
                                    
                                    cur.close()
                                    conn.close()
                                    
                                    if count > 0:
                                        st.session_state.longform_analysis_status = 'confirm_needed'
                                    else:
                                        st.session_state.longform_analysis_status = 'confirmed'
                                        on_analyze_longform_click_channel()
                                    
                                    st.rerun()

                                except Exception as e:
                                    st.error(f"분석 결과 확인 중 오류가 발생했습니다: {str(e)}")
                        
                        # 해당 채널 롱폼이 이미 분석된 적 있는 경우
                        if st.session_state.longform_analysis_status == 'confirm_needed':
                            st.warning("이 채널의 롱폼 영상은 이미 분석된 적이 있습니다. 그래도 다시 분석을 진행하시겠습니까?")

                            col1, col2 = st.columns(2)
                            with col1:
                                if st.button("예", key="confirm_longform_yes_ch"):
                                    st.session_state.longform_analysis_status = 'confirmed'
                                    on_analyze_longform_click_channel()  # 콜백 함수 호출
                                    st.rerun()
                            with col2:
                                if st.button("아니오", key="confirm_longform_no_ch"):
                                    st.session_state.longform_analysis_status = 'show_existing'
                                    st.rerun()
                        
                        if st.session_state.longform_analysis_status == 'show_existing':
                            try:
                                conn = connect_postgres()
                                cur = conn.cursor()
                                
                                # 이전 분석 결과 가져오기
                                cur.execute("""
                                SELECT search_unique_id, channel_result, created_at 
                                FROM analysis_channel 
                                WHERE search_unique_id = %s AND is_shorts = FALSE
                                ORDER BY created_at DESC
                                """, (st.session_state.selected_search_id,))
                                
                                existing_results = cur.fetchall()
                                
                                cur.close()
                                conn.close()
                                
                                if existing_results:
                                    st.subheader("기존 롱폼 분석 결과")
                                    
                                    for i, (search_id, analysis_result, created_at) in enumerate(existing_results):
                                        with st.expander(f"분석 결과 #{i+1} (생성일: {created_at})", expanded=(i==0)):
                                            st.markdown(analysis_result)
                                    
                                    # # 썸네일 분석 결과도 가져오기
                                    # try:
                                    #     conn = connect_postgres()
                                    #     cur = conn.cursor()
                                        
                                    #     cur.execute("""
                                    #     SELECT thumbnail_url, thumbnail_analysis 
                                    #     FROM analysis_thumbnail 
                                    #     WHERE search_unique_id = %s AND is_shorts = FALSE
                                    #     ORDER BY created_at DESC LIMIT 3
                                    #     """, (st.session_state.selected_search_id,))
                                        
                                    #     thumbnail_results = cur.fetchall()
                                        
                                    #     cur.close()
                                    #     conn.close()
                                        
                                    #     if thumbnail_results:
                                    #         st.write("### 인기 썸네일 분석")
                                    #         for i, (thumbnail_url, thumbnail_analysis) in enumerate(thumbnail_results):
                                    #             cols = st.columns([1, 2])
                                    #             with cols[0]:
                                    #                 st.image(thumbnail_url)
                                    #             with cols[1]:
                                    #                 st.markdown(thumbnail_analysis)
                                    # except Exception as e:
                                    #     st.error(f"썸네일 분석 결과 조회 중 오류가 발생했습니다: {str(e)}")
                                else:
                                    st.info("이 채널에 대한 기존 롱폼 분석 결과를 찾을 수 없습니다.")
                                
                                # 새 분석 시작 버튼
                                if st.button("새 분석 시작", key="new_longform_analysis_ch"):
                                    st.session_state.longform_analysis_status = 'initial'
                                    st.rerun()
                                    
                            except Exception as e:
                                st.error(f"기존 분석 결과 조회 중 오류가 발생했습니다: {str(e)}")
                        
                        # 분석 수행 및 결과 표시
                        if st.session_state.longform_analyzed_channel:
                            if st.session_state.longform_analysis_result_channel is None:
                                with st.spinner("롱폼 영상 분석 중..."):
                                    # 롱폼 분석 수행
                                    longform_analysis = analyze_channel_video(openai_client, llm_option, display_df, is_shorts=False)
                                    st.session_state.longform_analysis_result_channel = longform_analysis
                                    
                                    thumbnail_analysis_long = analyze_thumbnails(openai_client, display_df, is_shorts=False)
                                    st.session_state.longform_thumbnail_analysis_channel = thumbnail_analysis_long

                                    # 분석 내용 저장
                                    save_video_analysis('analysis_channel', st.session_state.selected_search_id, False, longform_analysis)  # save_video_analysis('channel_analysis', search_id_input, False, longform_analysis)
                                    save_thumbnail_analysis(thumbnail_analysis_long, st.session_state.selected_search_id, False, display_df['채널URL'].iloc[0])  # save_thumbnail_analysis(thumbnail_analysis_long, search_id_input, False, display_df['채널URL'].iloc[0])
                                    
                                    st.success("롱폼 영상 분석 완료 및 저장되었습니다!")

                                    # 분석 완료 후 상태 초기화
                                    st.session_state.longform_analysis_status = 'initial'
                            
                            # 저장된 분석 결과 표시
                            st.write(st.session_state.longform_analysis_result_channel)

                            st.write("### 인기 썸네일 분석")
                            if isinstance(st.session_state.longform_thumbnail_analysis_channel, list):
                                for analysis in st.session_state.longform_thumbnail_analysis_channel:
                                    st.write(f"#### {analysis['제목']}")
                                    cols = st.columns([1, 2])
                                    with cols[0]:
                                        st.image(analysis['썸네일'])
                                    with cols[1]:
                                        st.write(analysis['분석'])
                            else:
                                st.write(st.session_state.longform_thumbnail_analysis_channel)
            else:
                st.warning(f"검색 ID {st.session_state.selected_search_id}에 해당하는 데이터가 없습니다.")  # st.warning(f"검색 ID {search_id_input}에 해당하는 데이터가 없습니다.")
                st.session_state.found_data_channel = None
        except Exception as e:
            st.error(f"데이터 조회 중 오류가 발생했습니다: {str(e)}")
            st.session_state.found_data_channel = None
    
    st.markdown("---")
    
    try:
        st.subheader("쇼츠, 롱폼 동영상 분석 리스트")
        
        conn = connect_postgres()
        cur = conn.cursor()

        cur.execute("""
        SELECT 
            search_unique_id, is_shorts, channel_result, created_at
        FROM 
            analysis_channel
        ORDER BY 
            created_at DESC
        """)

        channel_results = cur.fetchall()
        
        cur.close()
        conn.close()
        
        channel_columns = ['검색ID', '쇼츠', '분석 내용', '생성일시']
        channel_df = pd.DataFrame(channel_results, columns=channel_columns)
        st.dataframe(
            channel_df,
            column_config={
                "검색ID": st.column_config.Column(width="small"),
                "쇼츠": st.column_config.Column(width="small"),
                "분석 내용": st.column_config.TextColumn(width="large"),
                "생성일시": st.column_config.DatetimeColumn(format="YYYY-MM-DD HH:mm")
            },
            hide_index=True,
            use_container_width=True
        )
    except Exception as e:
        st.error(f"분석된 채널 리스트 조회 중 오류가 발생했습니다: {str(e)}")

# 키워드 데이터 탭
with tab_keyword:
    if 'keyword_status' not in st.session_state:
        st.session_state.keyword_status = 'initial'  # 'initial', 'confirmed', 'show_existing'
    if 'current_keyword' not in st.session_state:
        st.session_state.current_keyword = None
    
    st.subheader("키워드 분석")
    query = st.text_input("분석하고 싶은 키워드를 입력하세요:")
    max_results = st.slider("분석할 영상 수", 10, 50, 30)
    search_button = st.button("검색 시작", type="primary", icon=":material/search:")

    if query and search_button:
        st.session_state.current_keyword = query

        try:
            # 있는 키워드를 경우 경고창
            conn = connect_postgres()
            cur = conn.cursor()

            cur.execute("""SELECT COUNT(*) FROM info_keyword WHERE keyword = %s """, (query,))
            
            count = cur.fetchone()[0]
            
            cur.close()
            conn.close()
            
            if count > 0:
                st.session_state.keyword_status = 'confirm_needed'
            else:
                st.session_state.keyword_status = 'confirmed'
            
            st.rerun()
        
        except Exception as e:
            st.error(f"에러가 발생했습니다: {str(e)}")
    elif search_button:
        st.warning("키워드를 입력하세요")

    
    if st.session_state.keyword_status == 'confirm_needed':
        st.warning("이미 분석된 적 있는 검색어입니다. 그래도 분석을 진행하시겠습니까?")
        
        col1, col2 = st.columns(2)
        with col1:
            if st.button("예", key="confirm_keyword_yes"):
                st.session_state.keyword_status = 'confirmed'
                st.rerun()
        with col2:
            if st.button("아니오", key="confirm_keyword_no"):
                st.session_state.keyword_status = 'show_existing'
                st.rerun()

    if st.session_state.keyword_status == 'show_existing':
        try:
            conn = connect_postgres()
            cur = conn.cursor()

            cur.execute("""
            SELECT search_unique_id, keyword, channel_name, video_title, video_view_subscriber_ratio, is_shorts
            FROM info_keyword
            WHERE keyword = %s
            ORDER BY search_unique_id DESC
            """, (st.session_state.current_keyword,))
            
            existing_results = cur.fetchall()
            
            cur.close()
            conn.close()

            if existing_results:
                st.subheader(f"키워드 '{st.session_state.current_keyword}'의 기존 분석 결과")
                
                df = pd.DataFrame(
                    existing_results,
                    columns=["ID", "키워드", "채널명", "영상 제목", "조회수/구독자 비율", "쇼츠"]
                )
                st.dataframe(
                    df, 
                    column_config={
                        "ID": st.column_config.Column(width="small"),
                        "키워드": st.column_config.Column(width="medium"),
                        "채널명": st.column_config.Column(width="medium"),
                        "영상 제목": st.column_config.Column(width="large"),
                        "조회수/구독자 비율": st.column_config.Column(width="medium"),
                        "쇼츠": st.column_config.Column(width="small")
                    },
                    hide_index=True,
                    use_container_width=True
                )
            else:
                st.info(f"해당 채널에 대한 결과를 찾을 수 없습니다.")
            
            # 상태 재설정을 위한 버튼
            if st.button("새 분석 시작", key='new_analysis_keyword'):
                st.session_state.keyword_status = 'initial'
                st.session_state.current_channel = None
                st.session_state.current_channel_keyword = None
                st.rerun()

        except Exception as e:
            st.error(f"결과 조회 중 오류가 발생했습니다: {str(e)}")
    
    # 키워드 분석 진행
    if st.session_state.keyword_status == 'confirmed' and st.session_state.current_keyword:
        with st.spinner("키워드 정보를 가져오는 중..."):
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
                        'info_keyword', pk_id, query, channel_url, video['channel'], video['subscribers'],
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
    
    st.markdown("---")
    
    st.subheader("저장된 키워드 데이터 조회")
    
    # 세션 상태 초기화
    if 'search_clicked_keyword' not in st.session_state:
        st.session_state.search_clicked_keyword = False
    if 'shorts_analyzed_keyword' not in st.session_state:  # 쇼츠 분석 완료 여부
        st.session_state.shorts_analyzed_keyword = False
    if 'longform_analyzed_keyword' not in st.session_state:  # 롱폼 분석 완료 여부
        st.session_state.longform_analyzed_keyword = False
    if 'shorts_analysis_result_keyword' not in st.session_state:  # 쇼츠 분석 결과
        st.session_state.shorts_analysis_result_keyword = None
    if 'longform_analysis_result_keyword' not in st.session_state:  # 롱폼 분석 결과
        st.session_state.longform_analysis_result_keyword = None
    if 'shorts_thumbnail_analysis_keyword' not in st.session_state:  # 추가
        st.session_state.shorts_thumbnail_analysis_keyword = None
    if 'longform_thumbnail_analysis_keyword' not in st.session_state:  # 추가
        st.session_state.longform_thumbnail_analysis_keyword = None
    if 'found_data_keyword' not in st.session_state:
        st.session_state.found_data_keyword = None
    
    # 모든 키워드별 최고 성과 동영상 표시
    st.info("키워드별 조회수/구독자 수 비율이 가장 높은 동영상입니다.")
                
    # ID 선택에 도움이 되는 정보 추가
    st.info("아래 목록에서 분석하고 싶은 키워드의 '분석' 버튼을 클릭하세요.")
    #
    try:
        top_videos_df = get_top_videos_by_search_id('info_keyword')
        
        if not top_videos_df.empty:
            search_keyword = st.text_input("조회할 키워드를 입력하세요.", key='search_keyword_kw')
            search_channel = st.text_input("조회할 채널명을 입력하세요.", key='search_channel_kw')
            
            filtered_df = top_videos_df.copy()
            
            # 키워드 필터링
            if search_keyword:
                filtered_df = filtered_df[filtered_df['키워드'].str.contains(search_keyword, case=False, na=False)]
            
            # 채널명 필터링
            if search_channel:
                filtered_df = filtered_df[filtered_df['채널명'].str.contains(search_channel, case=False, na=False)]
            
            # 버튼 동작을 위한 콜백 함수
            def analyze_keyword(search_id):
                st.session_state.search_clicked_keyword = True
                st.session_state.shorts_analyzed_keyword = False
                st.session_state.longform_analyzed_keyword = False
                st.session_state.shorts_analysis_result_keyword = None
                st.session_state.longform_analysis_result_keyword = None
                st.session_state.shorts_thumbnail_analysis_keyword = None
                st.session_state.longform_thumbnail_analysis_keyword = None
                st.session_state.found_data_keyword = None
                
                # 선택한 ID 저장
                st.session_state.selected_search_id_keyword = search_id
                
                # 데이터 로드
                display_df = load_info(search_id, 'info_keyword')
                st.session_state.found_data_keyword = display_df
            
            # 버튼 열과 데이터프레임을 나란히 배치
            col_buttons, col_table = st.columns([1, 7])

            with col_buttons:
                for _, row in filtered_df.iterrows():
                    search_id = row['pk_ID']
                    if st.button(f"📊 ID {search_id} 분석", key=f"btn_analyze_keyword_{search_id}"):
                        analyze_keyword(search_id)
            
            # # 각 행에 분석 버튼 추가를 위한 버튼 열 생성
            # top_videos_df['분석'] = top_videos_df['pk_ID'].apply(
            #     lambda x: f'<button key="analyze_{x}">분석</button>'
            # )

            # 데이터 표시
            with col_table:
                st.dataframe(
                    filtered_df,
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

                # top_vids = top_videos_df.copy()

                # # '분석' 열을 버튼 텍스트로 채우기
                # top_vids['분석'] = ''

                # # 각 행에 대해 버튼을 생성하고, 클릭 시 분석 실행
                # for index, row in top_vids.iterrows():
                #     # # 버튼을 각 행에 맞게 출력
                #     # if st.button(f"📊 ID {row['pk_ID']} 분석", key=f"btn_analyze_keyword_{row['pk_ID']}"):
                #     #     analyze_keyword(row['pk_ID'])  # 버튼 클릭 시 분석 함수 호출
                #     #     top_vids['분석'] = f"📊 ID {row['pk_ID']} 분석"
                #     top_vids['분석'] = st.button('분석', key=f'something_{index}')

                # # 버튼이 포함된 데이터프레임을 HTML로 변환하여 표시
                # display = top_vids[['분석', 'pk_ID', '키워드', '채널명', '제목', '조회수', '조회수/구독자 비율', '쇼츠']]

                # # 테이블을 HTML로 렌더링하여 표시
                # st.markdown(display.to_html(escape=False, index=False), unsafe_allow_html=True)
            
            # 도움말 메시지 수정
            st.info("👆 위 목록에서 분석하고 싶은 키워드의 '분석' 버튼을 클릭하세요.")
        else:
            st.warning("저장된 채널 데이터가 없습니다.")
    except Exception as e:
        st.error(f"데이터 조회 중 오류가 발생했습니다: {str(e)}")
    #

    # # 특정 채널 상세 분석 섹션
    # st.subheader("특정 검색ID 상세 분석")
    # search_id_input = st.number_input("키워드에 대해 분석할 pk_ID를 입력하세요", min_value=1, step=1)
        
    # 쇼츠 분석 버튼 콜백
    def on_analyze_shorts_click_keyword():
        st.session_state.shorts_analyzed_keyword = True
    
    # 롱폼 분석 버튼 콜백
    def on_analyze_longform_click_keyword():
        st.session_state.longform_analyzed_keyword = True
    
    # search_button_keyword = st.button("분석 시작", type="primary", key="search_button_keyword_tab4", on_click=on_search_click_tab4)
    
    # 선택된 ID 표시
    if 'selected_search_id_keyword' in st.session_state:
        st.subheader(f"선택한 키워드 ID {st.session_state.selected_search_id_keyword} 분석 결과")

    # 검색 결과 표시
    if st.session_state.search_clicked_keyword:
        try:
            if 'found_data_keyword' not in st.session_state or st.session_state.found_data_keyword is None:
                display_df = load_info(st.session_state.selected_search_id_keyword, 'info_keyword')  # display_df = load_info(search_id_input, 'info_keyword')
                st.session_state.found_data_keyword = display_df
            else:
                display_df = st.session_state.found_data_keyword
            
            if not display_df.empty:
                st.success(f"검색 ID {st.session_state.selected_search_id_keyword}에 해당하는 데이터를 찾았습니다.")  # st.success(f"검색 ID {search_id_input}에 해당하는 데이터를 찾았습니다.")
                
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
                
                # 분석 섹션
                st.subheader("키워드 데이터 분석하기")

                col_shorts, col_long = st.columns(2)
                
                with col_shorts:
                    # 1. 쇼츠 분석 섹션
                    if 'shorts_analysis_status' not in st.session_state:
                        st.session_state.shorts_analysis_status = 'initial'  # 'initial', 'confirm_needed', 'confirmed', 'show_existing'
                    
                    st.write("### 쇼츠 영상 분석")
                    
                    if len(shorts_df) == 0:
                        st.info("해당 키워드에 대한 쇼츠 결과가 없습니다.")
                    else:
                        # 쇼츠 분석 버튼
                        if not st.session_state.shorts_analyzed_keyword:
                            shorts_btn = st.button(
                                "쇼츠 분석 시작", 
                                type="primary", 
                                key="btn_analyze_shorts_keyword",
                                # on_click=on_analyze_shorts_click_keyword
                            )

                            if shorts_btn:
                                try:
                                    conn = connect_postgres()
                                    cur = conn.cursor()
                    
                                    cur.execute("""
                                    SELECT COUNT(*) FROM analysis_keyword
                                    WHERE search_unique_id = %s AND is_shorts = TRUE
                                    """, (st.session_state.selected_search_id_keyword,))
                                    
                                    count = cur.fetchone()[0]
                                    
                                    cur.close()
                                    conn.close()
                                    
                                    if count > 0:
                                        st.session_state.shorts_analysis_status = 'confirm_needed'
                                    else:
                                        st.session_state.shorts_analysis_status = 'confirmed'
                                        on_analyze_shorts_click_keyword()
                                    
                                    st.rerun()

                                except Exception as e:
                                    st.error(f"분석 결과 확인 중 오류가 발생했습니다: {str(e)}")
                        
                        # 해당 채널 쇼츠가 이미 분석된 적 있는 경우
                        if st.session_state.shorts_analysis_status == 'confirm_needed':
                            st.warning("해당 키워드의 쇼츠 영상은 이미 분석된 적이 있습니다. 그래도 다시 분석을 진행하시겠습니까?")

                            col1, col2 = st.columns(2)
                            with col1:
                                if st.button("예", key="confirm_shorts_yes_kw"):
                                    st.session_state.shorts_analysis_status = 'confirmed'
                                    on_analyze_shorts_click_keyword()  # 콜백 함수 호출
                                    st.rerun()
                            with col2:
                                if st.button("아니오", key="confirm_shorts_no_kw"):
                                    st.session_state.shorts_analysis_status = 'show_existing'
                                    st.rerun()
                        
                        if st.session_state.shorts_analysis_status == 'show_existing':
                            try:
                                conn = connect_postgres()
                                cur = conn.cursor()
                                
                                # 이전 분석 결과 가져오기
                                cur.execute("""
                                SELECT search_unique_id, keyword_result, created_at 
                                FROM analysis_keyword 
                                WHERE search_unique_id = %s AND is_shorts = TRUE
                                ORDER BY created_at DESC
                                """, (st.session_state.selected_search_id_keyword,))
                                
                                existing_results = cur.fetchall()
                                
                                cur.close()
                                conn.close()
                                
                                if existing_results:
                                    st.subheader("기존 쇼츠 분석 결과")
                                    
                                    for i, (search_id, analysis_result, created_at) in enumerate(existing_results):
                                        with st.expander(f"분석 결과 #{i+1} (생성일: {created_at})", expanded=(i==0)):
                                            st.markdown(analysis_result)
                                    
                                    # # 썸네일 분석 결과도 가져오기
                                    # try:
                                    #     conn = connect_postgres()
                                    #     cur = conn.cursor()
                                        
                                    #     cur.execute("""
                                    #     SELECT thumbnail_url, thumbnail_analysis 
                                    #     FROM analysis_thumbnail 
                                    #     WHERE search_unique_id = %s AND is_shorts = TRUE
                                    #     ORDER BY created_at DESC LIMIT 3
                                    #     """, (st.session_state.selected_search_id_keyword,))
                                        
                                    #     thumbnail_results = cur.fetchall()
                                        
                                    #     cur.close()
                                    #     conn.close()
                                        
                                    #     if thumbnail_results:
                                    #         st.write("### 인기 썸네일 분석")
                                    #         for i, (thumbnail_url, thumbnail_analysis) in enumerate(thumbnail_results):
                                    #             cols = st.columns([1, 2])
                                    #             with cols[0]:
                                    #                 st.image(thumbnail_url)
                                    #             with cols[1]:
                                    #                 st.markdown(thumbnail_analysis)
                                    # except Exception as e:
                                    #     st.error(f"썸네일 분석 결과 조회 중 오류가 발생했습니다: {str(e)}")
                                else:
                                    st.info("해당 키워드의 기존 쇼츠 분석 결과를 찾을 수 없습니다.")
                                
                                # 새 분석 시작 버튼
                                if st.button("새 분석 시작", key="new_shorts_analysis_kw"):
                                    st.session_state.shorts_analysis_status = 'initial'
                                    st.rerun()
                                    
                            except Exception as e:
                                st.error(f"기존 분석 결과 조회 중 오류가 발생했습니다: {str(e)}")
                        
                        # 분석 수행 및 결과 표시
                        if st.session_state.shorts_analyzed_keyword:
                            if st.session_state.shorts_analysis_result_keyword is None:
                                with st.spinner("쇼츠 영상 분석 중..."):
                                    # 쇼츠 분석 수행
                                    shorts_analysis = analyze_keyword_video(openai_client, llm_option, display_df, is_shorts=True)
                                    st.session_state.shorts_analysis_result_keyword = shorts_analysis

                                    thumbnail_analysis_shorts = analyze_thumbnails(openai_client, display_df, is_shorts=True)
                                    st.session_state.shorts_thumbnail_analysis_keyword = thumbnail_analysis_shorts
                                    
                                    # 분석 내용 저장
                                    save_video_analysis_keyword('analysis_keyword', st.session_state.selected_search_id_keyword, True, shorts_analysis)  # save_video_analysis('keyword_analysis', search_id_input, True, shorts_analysis)
                                    save_thumbnail_analysis(thumbnail_analysis_shorts, st.session_state.selected_search_id_keyword, True, display_df['채널URL'].iloc[0])  # save_thumbnail_analysis(thumbnail_analysis_shorts, search_id_input, True, display_df['채널URL'].iloc[0])
                                    
                                    st.success("쇼츠 영상 분석 완료 및 저장되었습니다!")

                                    # 분석 완료 후 상태 초기화
                                    st.session_state.shorts_analysis_status = 'initial'
                            
                            # 저장된 분석 결과 표시
                            st.write(st.session_state.shorts_analysis_result_keyword)

                            # 썸네일 분석 결과 표시
                            st.write("### 인기 썸네일 분석")
                            if isinstance(st.session_state.shorts_thumbnail_analysis_keyword, list):
                                for analysis in st.session_state.shorts_thumbnail_analysis_keyword:
                                    st.write(f"#### {analysis['제목']}")
                                    cols = st.columns([1, 2])
                                    with cols[0]:
                                        st.image(analysis['썸네일'])
                                    with cols[1]:
                                        st.write(analysis['분석'])
                            else:
                                st.write(st.session_state.shorts_thumbnail_analysis_keyword)
                
                # # 구분선
                # st.markdown("---")
                
                with col_long:
                    # 2. 롱폼 분석 섹션
                    if 'longform_analysis_status' not in st.session_state:
                        st.session_state.longform_analysis_status = 'initial'  # 'initial', 'confirm_needed', 'confirmed', 'show_existing'
                    
                    st.write("### 롱폼 영상 분석")
                    
                    if len(longform_df) == 0:
                        st.info("해당 키워드에 대한 롱폼 결과가 없습니다.")
                    else:
                        # 롱폼 분석 버튼
                        if not st.session_state.longform_analyzed_keyword:
                            longform_btn = st.button(
                                "롱폼 분석 시작", 
                                type="primary", 
                                key="btn_analyze_longform_keyword",
                                # on_click=on_analyze_longform_click_keyword
                            )

                            if longform_btn:
                                try:
                                    conn = connect_postgres()
                                    cur = conn.cursor()
                    
                                    cur.execute("""
                                    SELECT COUNT(*) FROM analysis_keyword 
                                    WHERE search_unique_id = %s AND is_shorts = FALSE
                                    """, (st.session_state.selected_search_id_keyword,))
                                    
                                    count = cur.fetchone()[0]
                                    
                                    cur.close()
                                    conn.close()
                                    
                                    if count > 0:
                                        st.session_state.longform_analysis_status = 'confirm_needed'
                                    else:
                                        st.session_state.longform_analysis_status = 'confirmed'
                                        on_analyze_longform_click_keyword()
                                    
                                    st.rerun()
                                
                                except Exception as e:
                                    st.error(f"분석 결과 확인 중 오류가 발생했습니다: {str(e)}")
                        
                        # 해당 채널 롱폼이 이미 분석된 적 있는 경우
                        if st.session_state.longform_analysis_status == 'confirm_needed':
                            st.warning("해당 키워드의 롱폼 영상은 이미 분석된 적이 있습니다. 그래도 다시 분석을 진행하시겠습니까?")

                            col1, col2 = st.columns(2)
                            with col1:
                                if st.button("예", key="confirm_longform_yes_kw"):
                                    st.session_state.longform_analysis_status = 'confirmed'
                                    on_analyze_longform_click_keyword()  # 콜백 함수 호출
                                    st.rerun()
                            with col2:
                                if st.button("아니오", key="confirm_longform_no_kw"):
                                    st.session_state.longform_analysis_status = 'show_existing'
                                    st.rerun()
                        
                        if st.session_state.longform_analysis_status == 'show_existing':
                            try:
                                conn = connect_postgres()
                                cur = conn.cursor()
                                
                                # 이전 분석 결과 가져오기
                                cur.execute("""
                                SELECT search_unique_id, keyword_result, created_at 
                                FROM analysis_keyword 
                                WHERE search_unique_id = %s AND is_shorts = FALSE
                                ORDER BY created_at DESC
                                """, (st.session_state.selected_search_id_keyword,))
                                
                                existing_results = cur.fetchall()
                                
                                cur.close()
                                conn.close()
                                
                                if existing_results:
                                    st.subheader("기존 롱폼 분석 결과")
                                    
                                    for i, (search_id, analysis_result, created_at) in enumerate(existing_results):
                                        with st.expander(f"분석 결과 #{i+1} (생성일: {created_at})", expanded=(i==0)):
                                            st.markdown(analysis_result)
                                    
                                    # # 썸네일 분석 결과도 가져오기
                                    # try:
                                    #     conn = connect_postgres()
                                    #     cur = conn.cursor()
                                        
                                    #     cur.execute("""
                                    #     SELECT thumbnail_url, thumbnail_analysis 
                                    #     FROM analysis_thumbnail 
                                    #     WHERE search_unique_id = %s AND is_shorts = FALSE
                                    #     ORDER BY created_at DESC LIMIT 3
                                    #     """, (st.session_state.selected_search_id_keyword,))
                                        
                                    #     thumbnail_results = cur.fetchall()
                                        
                                    #     cur.close()
                                    #     conn.close()
                                        
                                    #     if thumbnail_results:
                                    #         st.write("### 인기 썸네일 분석")
                                    #         for i, (thumbnail_url, thumbnail_analysis) in enumerate(thumbnail_results):
                                    #             cols = st.columns([1, 2])
                                    #             with cols[0]:
                                    #                 st.image(thumbnail_url)
                                    #             with cols[1]:
                                    #                 st.markdown(thumbnail_analysis)
                                    # except Exception as e:
                                    #     st.error(f"썸네일 분석 결과 조회 중 오류가 발생했습니다: {str(e)}")
                                else:
                                    st.info("이 채널에 대한 기존 롱폼 분석 결과를 찾을 수 없습니다.")
                                
                                # 새 분석 시작 버튼
                                if st.button("새 분석 시작", key="new_longform_analysis_kw"):
                                    st.session_state.longform_analysis_status = 'initial'
                                    st.rerun()
                                    
                            except Exception as e:
                                st.error(f"기존 분석 결과 조회 중 오류가 발생했습니다: {str(e)}")
                        
                        # 분석 수행 및 결과 표시
                        if st.session_state.longform_analyzed_keyword:
                            if st.session_state.longform_analysis_result_keyword is None:
                                with st.spinner("롱폼 영상 분석 중..."):
                                    # 롱폼 분석 수행
                                    longform_analysis = analyze_keyword_video(openai_client, llm_option, display_df, is_shorts=False)
                                    st.session_state.longform_analysis_result_keyword = longform_analysis

                                    thumbnail_analysis_long = analyze_thumbnails(openai_client, display_df, is_shorts=False)
                                    st.session_state.longform_thumbnail_analysis_keyword = thumbnail_analysis_long
                                    
                                    # 분석 내용 저장
                                    save_video_analysis_keyword('analysis_keyword', st.session_state.selected_search_id_keyword, False, longform_analysis)  # save_video_analysis('keyword_analysis', search_id_input, False, longform_analysis)
                                    save_thumbnail_analysis(thumbnail_analysis_long, st.session_state.selected_search_id_keyword, False, display_df['채널URL'].iloc[0])  # save_thumbnail_analysis(thumbnail_analysis_long, search_id_input, False, display_df['채널URL'].iloc[0])
                                    
                                    st.success("롱폼 영상 분석 완료 및 저장되었습니다!")

                                    # 분석 완료 후 상태 초기화
                                    st.session_state.longform_analysis_status = 'initial'
                            
                            # 저장된 분석 결과 표시
                            st.write(st.session_state.longform_analysis_result_keyword)

                            # 썸네일 분석 결과 표시
                            st.write("### 인기 썸네일 분석")
                            if isinstance(st.session_state.longform_thumbnail_analysis_keyword, list):
                                for analysis in st.session_state.longform_thumbnail_analysis_keyword:
                                    st.write(f"#### {analysis['제목']}")
                                    cols = st.columns([1, 2])
                                    with cols[0]:
                                        st.image(analysis['썸네일'])
                                    with cols[1]:
                                        st.write(analysis['분석'])
                            else:
                                st.write(st.session_state.longform_thumbnail_analysis_keyword)
            else:
                st.warning(f"검색 ID {st.session_state.selected_search_id_keyword}에 해당하는 데이터가 없습니다.")
                st.session_state.found_data_keyword = None
        except Exception as e:
            st.error(f"데이터 조회 중 오류가 발생했습니다: {str(e)}")
            st.session_state.found_data_keyword = None
    
    # 키워드 분석 내용 정리
    st.markdown("---")

    try:
        st.subheader("쇼츠, 롱폼 동영상 분석 리스트")
        
        conn = connect_postgres()
        cur = conn.cursor()

        cur.execute("""
        SELECT 
            search_unique_id, is_shorts, keyword_result, created_at
        FROM 
            analysis_keyword
        ORDER BY
            created_at DESC
        """)

        keyword_results = cur.fetchall()
        
        cur.close()
        conn.close()
        
        keyword_columns = ['검색ID', '쇼츠', '분석 내용', '생성일시']
        keyword_df = pd.DataFrame(channel_results, columns=keyword_columns)
        st.dataframe(
            keyword_df,
            column_config={
                "검색ID": st.column_config.Column(width="small"),
                "쇼츠": st.column_config.Column(width="small"),
                "분석 내용": st.column_config.TextColumn(width="large"),
                "생성일시": st.column_config.DatetimeColumn(format="YYYY-MM-DD HH:mm")
            },
            hide_index=True,
            use_container_width=True
        )
    except Exception as e:
        st.error(f"분석된 키워드 리스트 조회 중 오류가 발생했습니다: {str(e)}")

# 블로그 요약 탭
with tab_blog:
    st.subheader("블로그 요약")
    analysis_keyword = st.text_input('블로그 요약을 위한 키워드를 입력하세요. 분석 그룹의 이름을 결정합니다.')
    
    # 세션 상태에 URL 리스트 초기화
    if 'blog_urls' not in st.session_state:
        st.session_state.blog_urls = [""] * 10  # 10개의 빈 URL로 초기화

    st.info("요약할 블로그 게사물들의 주소를 입력하세요.")
    
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
    analyse_button = st.button("블로그 요약 시작", type="primary", disabled=len(valid_urls) == 0 or not analysis_keyword)
    
    # 블로그 요약 실행
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
                with st.spinner(f"블로그 요약 중... ({i+1}/{len(valid_urls)})"):
                    extracted_data = blog_content(url)  # 블로그 내용 추출
                    
                    blog_summary = blog_summarizer(openai_client, llm_option, extracted_data['content'])  # 블로그 요약
                    
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
            st.subheader("블로그 요약 결과")
            
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
                st.error(f"{len(failed_urls)}개의 블로그 요약에 실패했습니다.")
                for url, error in failed_urls:
                    with st.expander(f"실패한 URL: {url}"):
                        st.error(f"오류: {error}")
    
    st.markdown("---")
    
    st.subheader("블로그 통합 요약")

    st.info("키워드별 블로그 중 첫 번째 블로그에 대한 요약 내용입니다.")
    
    # 모든 키워드별 블로그 요약 데이터 조회
    try:
        conn = connect_postgres()
        cur = conn.cursor()
        
        # 키워드별로 그룹화하여 첫 번째 블로그 요약만 가져오기
        cur.execute("""
        WITH RankedSummaries AS (
            SELECT id, keyword, summary, url, ROW_NUMBER() OVER (PARTITION BY keyword ORDER BY id) as rn
            FROM blog_summary
        )
        SELECT id, keyword, summary, url FROM RankedSummaries
        WHERE rn = 1
        ORDER BY keyword
        """)
        
        keyword_summaries = cur.fetchall()
        cur.close()
        conn.close()
        
        if keyword_summaries:
            # 테이블용 데이터 준비
            table_data = []
            for summary_id, keyword, summary, url in keyword_summaries:
                # 요약 텍스트가 너무 길면 잘라내기
                short_summary = summary[:100] + "..." if len(summary) > 100 else summary
                table_data.append({"ID": summary_id, "키워드": keyword, "요약 미리보기": short_summary, "URL": url})
            
            # 데이터프레임으로 변환
            df = pd.DataFrame(table_data)

            col1, col2 = st.columns([1, 7])
            
            with col1:
                # 각 행마다 버튼 생성
                for i, row in df.iterrows():
                    load_keyword = row['키워드']
                    if st.button(f"📊 {load_keyword} 분석", key=f"btn_analyze_{load_keyword}_{i}", help=f"키워드 '{load_keyword}' 분석"):
                        # 세션 상태 설정
                        st.session_state.current_blog_keyword = load_keyword
                        
                        # 데이터베이스 확인
                        conn = connect_postgres()
                        cur = conn.cursor()
                        
                        cur.execute("""SELECT COUNT(*) FROM blog_int_summary WHERE keyword = %s """, (load_keyword,))
                        count = cur.fetchone()[0]
                        
                        cur.close()
                        conn.close()
                        
                        # 결과에 따라 상태 설정
                        if count > 0:
                            st.session_state.blog_keyword_status = 'confirm_needed'
                        else:
                            st.session_state.blog_keyword_status = 'confirmed'
                        
                        # 페이지 강제 새로고침
                        st.rerun()
            
            with col2:
                # 테이블 표시
                st.dataframe(
                    df,
                    column_config={
                        "ID": st.column_config.Column(width="small"),
                        "키워드": st.column_config.Column(width="medium"),
                        "요약 미리보기": st.column_config.Column(width="large"),
                        "URL": st.column_config.Column(width="medium")
                    },
                    hide_index=True,
                    use_container_width=True
                )
            
            
            # 상세 내용 보기 섹션
            selected_id = st.selectbox(
                "상세 내용을 확인할 키워드 ID를 선택하세요:",
                options=[row[0] for row in keyword_summaries],
                format_func=lambda x: f"ID: {x} - 키워드: {df[df['ID']==x]['키워드'].values[0]}"
            )
            
            if selected_id:
                # 선택한 ID의 요약 내용 표시
                selected_summary = next((s for s_id, k, s, u in keyword_summaries if s_id == selected_id), None)
                if selected_summary:
                    with st.expander("블로그 요약 내용", expanded=True):
                        st.markdown(selected_summary)

        else:
            st.info("저장된 블로그 요약이 없습니다.")
            
    except Exception as e:
        st.error(f"블로그 요약 데이터 조회 중 오류가 발생했습니다: {str(e)}")
        # 에러 발생 시 연결 닫기
        try:
            if 'conn' in locals() and conn:
                conn.rollback()
                cur.close()
                conn.close()
        except:
            pass
    
    # 세션 상태 초기화 - 이름을 더 명확하게 지정
    if 'blog_keyword_status' not in st.session_state:
        st.session_state.blog_keyword_status = 'initial'  # 'initial', 'confirmed', 'show_existing'
    if 'current_blog_keyword' not in st.session_state:
        st.session_state.current_blog_keyword = None
    if 'blog_id' not in st.session_state:
        st.session_state.blog_id = None

    # 확인이 필요한 경우 - 메시지와 버튼 표시
    if st.session_state.blog_keyword_status == 'confirm_needed':
        st.warning(f"키워드 '{st.session_state.current_blog_keyword}'는 이미 통합 분석된 적 있습니다. 그래도 분석을 진행하시겠습니까?")
        
        col1, col2 = st.columns(2)
        with col1:
            if st.button("예", key="confirm_yes"):
                st.session_state.blog_keyword_status = 'confirmed'
                st.rerun()
        with col2:
            if st.button("아니오", key="confirm_no"):
                st.session_state.blog_keyword_status = 'show_existing'
                st.rerun()

    # 기존 분석 결과 표시
    if st.session_state.blog_keyword_status == 'show_existing':
        try:
            conn = connect_postgres()
            cur = conn.cursor()
            
            cur.execute("""
            SELECT search_unique_id, int_summary
            FROM blog_int_summary 
            WHERE keyword = %s 
            """, (st.session_state.current_blog_keyword,))
            
            existing_results = cur.fetchall()
            
            cur.close()
            conn.close()
            
            if existing_results:
                st.subheader(f"키워드 '{st.session_state.current_blog_keyword}'의 기존 통합 분석 결과")
                
                # for i, (search_id, summary) in enumerate(existing_results):
                #     with st.expander(f"분석 결과 #{i+1} (ID: {search_id}", expanded=(i==0)):
                #         st.markdown(summary)
                
                int_sum_df = pd.DataFrame(existing_results, columns=['ID', '요약'])
                st.dataframe(
                    int_sum_df, 
                    column_config={
                        "ID": st.column_config.Column(width="small"),
                        "요약": st.column_config.Column(width="large"),
                    },
                    hide_index=True,
                    use_container_width=True
                )
            else:
                st.info(f"키워드 '{st.session_state.current_blog_keyword}'에 대한 통합 분석 결과를 찾을 수 없습니다.")
            
            # 상태 재설정을 위한 버튼
            if st.button("새 분석 시작"):
                st.session_state.blog_keyword_status = 'initial'
                st.session_state.current_blog_keyword = None
                st.rerun()
         
        except Exception as e:
            st.error(f"결과 조회 중 오류가 발생했습니다: {str(e)}")
            
    # 분석된 적 없는 키워드인 경우 그냥 분석 진행
    if st.session_state.blog_keyword_status == 'confirmed' and st.session_state.current_blog_keyword:
        with st.spinner(f"키워드 '{st.session_state.current_blog_keyword}'로 저장된 블로그 요약을 통합 분석 중..."):
            try:
                # 데이터베이스에서 해당 키워드로 저장된 요약 조회
                conn = connect_postgres()
                cur = conn.cursor()
                
                cur.execute("""
                SELECT summary FROM blog_summary 
                WHERE keyword = %s
                """, (st.session_state.current_blog_keyword,))
                
                summaries = [row[0] for row in cur.fetchall()]
                
                # 조회된 요약이 없는 경우
                if not summaries:
                    st.error(f"키워드 '{st.session_state.current_blog_keyword}'로 저장된 블로그 요약이 없습니다.")
                else:
                    # 통합 분석을 위한 프롬프트 작성
                    prompt = f"""다음은 "{st.session_state.current_blog_keyword}" 키워드와 관련된 {len(summaries)}개의 블로그 포스트 요약입니다:"""
                    
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
                        model=llm_option, 
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
                st.session_state.blog_keyword_status = 'initial'

# 콘텐츠 생성하기 탭
with tab_content:
    st.subheader("콘텐츠 생성하기")
    
    # 세션 상태 초기화
    if 'content_generated_yt' not in st.session_state:
        st.session_state.content_generated_yt = False
    
    # 세션 상태 초기화
    if 'content_generated_ig' not in st.session_state:
        st.session_state.content_generated_ig = False
        
    # 세션 상태 초기화
    if 'content_generated_th' not in st.session_state:
        st.session_state.content_generated_th = False
    
    # 블로그 키워드 리스트
    try:
        conn = connect_postgres()
        cur = conn.cursor()
        
        # 모든 통합 블로그 요약 데이터 조회
        cur.execute("""SELECT search_unique_id, keyword FROM blog_int_summary""")
        
        blog_summaries = cur.fetchall()
        cur.close()
        conn.close()
        
        # 데이터 표시
        if blog_summaries:
            st.subheader("블로그 키워드")

            st.info("통합 요약된적 있는 블로그 키워드입니다.")
            
            # 데이터프레임으로 변환
            df = pd.DataFrame(blog_summaries, columns=['분석ID', '키워드'])
            
            # 데이터프레임 표시
            st.dataframe(
                df,
                column_config={
                    "분석ID": st.column_config.Column(width="small"),
                    "키워드": st.column_config.Column(width="medium"),
                },
                hide_index=True,
                use_container_width=True
            )
            
            # 통합 분석 내용 보기 섹션
            selected_id = st.selectbox(
                "상세 내용을 확인할 블로그 요약 ID를 선택하세요:",
                options=[row[0] for row in blog_summaries],
                format_func=lambda x: f"ID: {x} - 키워드: {df[df['분석ID']==x]['키워드'].values[0]}"
            )
            
            if selected_id:
                # 선택한 ID의 통합 분석 내용 가져오기
                conn = connect_postgres()
                cur = conn.cursor()
                cur.execute("""SELECT int_summary FROM blog_int_summary WHERE search_unique_id = %s""", (selected_id,))
                
                summary_result = cur.fetchone()
                cur.close()
                conn.close()
                
                if summary_result:
                    with st.expander("블로그 통합 분석 내용", expanded=True):
                        st.markdown(summary_result[0])
        else:
            st.info("저장된 블로그 통합 분석 내용이 없습니다.")
    
    except Exception as e:
        st.error(f"블로그 통합 분석 데이터 조회 중 오류가 발생했습니다: {str(e)}")

    blog_id = st.text_input('콘텐츠의 주제로 삼을 블로그 요약본의 분석 아이디를 입력하세요.')

    if 'current_blog_id' not in st.session_state:
        st.session_state.current_blog_id = None
    if 'youtube_status' not in st.session_state:
        st.session_state.youtube_status = 'initial'  # 'initial', 'confirmed', 'show_existing'
    if 'instagram_status' not in st.session_state:
        st.session_state.instagram_status = 'initial'  # 'initial', 'confirmed', 'show_existing'
    if 'threads_status' not in st.session_state:
        st.session_state.threads_status = 'initial'  # 'initial', 'confirmed', 'show_existing'
    
    # 콘텐츠 생성 버튼
    yt_button = st.button("유튜브 콘텐츠 만들기", type="primary")
    insta_button = st.button("인스타 콘텐츠 만들기", type="primary")
    thrd_button = st.button("쓰레드 콘텐츠 만들기", type="primary")

    # 유튜브 콘텐츠 생성
    if yt_button and blog_id:
        st.session_state.current_blog_id = blog_id

        # 블로그 ID 확인
        conn = connect_postgres()
        cur = conn.cursor()
        
        # blog_int_summary에서 확인
        cur.execute("""SELECT COUNT(*) FROM blog_int_summary WHERE search_unique_id = %s """, (blog_id,))
        blog_exists = cur.fetchone()[0]
        
        if blog_exists == 0:
            st.error(f"입력한 블로그 ID '{blog_id}'를 찾을 수 없습니다.")
        else:
            # 이미 유튜브 콘텐츠가 있는지 확인
            cur.execute("""SELECT COUNT(*) FROM content_youtube WHERE blog_id = %s """, (blog_id,))
            content_exists = cur.fetchone()[0]
            
            if content_exists > 0:
                st.session_state.youtube_status = 'confirm_needed'
            else:
                st.session_state.youtube_status = 'confirmed'
        
        cur.close()
        conn.close()
        st.rerun()
    elif yt_button and not blog_id:
        st.warning("블로그 요약 ID를 입력해주세요.")
        
    # 생성전에 확인
    if st.session_state.youtube_status == 'confirm_needed':
        st.warning(f"아이디 '{st.session_state.current_blog_id}' 블로그 내용으로는 이미 유튜브 콘텐츠를 생성한 적이 있습니다. 그래도 생성하시겠습니까?")
    
        col1, col2 = st.columns(2)
        with col1:
            if st.button("예", key="confirm_yes_yt"):
                st.session_state.youtube_status = 'confirmed'
                st.rerun()
        with col2:
            if st.button("아니오", key="confirm_no_yt"):
                st.session_state.youtube_status = 'show_existing'
                st.rerun()
        
    if st.session_state.youtube_status == 'show_existing':
        try:
            conn = connect_postgres()
            cur = conn.cursor()

            cur.execute("""
            SELECT blog_id, keyword, title, created_at
            FROM content_youtube 
            WHERE blog_id = %s 
            ORDER BY created_at DESC
            """, (st.session_state.current_blog_id,))

            existing_results = cur.fetchall()
        
            cur.close()
            conn.close()

            
            if existing_results:
                df = pd.DataFrame(existing_results)
                st.dataframe(
                    df, 
                    column_config={
                        "블로그 ID": st.column_config.Column(width="small"),
                        "키워드": st.column_config.Column(width="medium"),
                        "제목": st.column_config.Column(width="large"),
                        "스크립트 미리보기": st.column_config.Column(width="large"),
                        "생성일": st.column_config.Column(width="medium")
                    }, 
                    hide_index=True,
                    use_container_width=True
                )

            if st.button("새 콘텐츠 생성", key="new_youtube_content"):
                st.session_state.youtube_status = 'initial'
                st.session_state.current_blog_id = None
                st.rerun()
        
        except Exception as e:
            st.error(f"결과 조회 중 오류가 발생했습니다: {str(e)}")

    if st.session_state.youtube_status == 'confirmed' and st.session_state.current_blog_id:
        with st.spinner('유튜브 콘텐츠를 생성하는 중입니다...'):
            try:
                conn = connect_postgres()
                cur = conn.cursor()
                
                # 블로그 통합 요약 내용 불러오기
                cur.execute("""SELECT int_summary, keyword FROM blog_int_summary WHERE search_unique_id = %s""", (blog_id,))
                result = cur.fetchone()

                # 긍정적인 평가 내용 불러오기
                cur.execute("""
                SELECT score, feedback, title, thumbnail, script
                FROM feedback_yt
                WHERE platform = 'YouTube' AND score >= 7
                ORDER BY score DESC
                LIMIT 3
                """)
                high_feedback = cur.fetchall()

                # 부정적인 평가 내용 불러오기
                cur.execute("""
                SELECT score, feedback, title, thumbnail, script
                FROM feedback_yt
                WHERE platform = 'YouTube' AND score <= 4
                ORDER BY score ASC
                LIMIT 3
                """)
                low_feedback = cur.fetchall()
                
                cur.close()
                conn.close()
                    
                if result:
                    blog_summary = result[0]
                    keyword = result[1]
                else:
                    st.error(f"ID {blog_id}에 해당하는 블로그 요약을 찾을 수 없습니다.")
                    st.stop()

                # 피드백 데이터 요약 및 통합
                high_feedback_summary = "좋은 평가를 받은 콘텐츠의 특징:\n"
                if high_feedback:
                    # 높은 점수 피드백의 주요 내용 통합
                    for i, (score, feedback, title, _, _) in enumerate(high_feedback):
                        high_feedback_summary += f"{i+1}. 점수 {score}/10: {feedback}\n"
                        
                    # 좋은 예시 제목 추가
                    high_feedback_summary += "\n좋은 평가를 받은 제목 예시:\n"
                    for i, (_, _, title, _, _) in enumerate(high_feedback[:3]):  # 상위 3개만
                        high_feedback_summary += f"- {title}\n"
                else:
                    high_feedback_summary += "아직 충분한 데이터가 없습니다.\n"
                
                low_feedback_summary = "개선이 필요한 콘텐츠의 특징 (피해야 할 점):\n"
                if low_feedback:
                    # 낮은 점수 피드백의 주요 내용 통합
                    for i, (score, feedback, _, _, _) in enumerate(low_feedback):
                        low_feedback_summary += f"{i+1}. 점수 {score}/10: {feedback}\n"
                else:
                    low_feedback_summary += "아직 충분한 데이터가 없습니다.\n"

                prompt = f"""
새로 만들 유튜브 동영상을 위한 제목, 썸네일 이미지 내용, 첫 2분 스크립트 내용을 생성해야 합니다.

제목 및 썸네일 이미지 내용은 다음 카피라이팅 법칙 5가지를 꼭 지켜서 생성해주세요:
1. NUMBERS (구체적 숫자 / 전후 비교)
2. ONE & ONLY (하나만 지키면 된다 / 단 하나의 비밀)
3. SHOCK & HOOK (충격·호기심 + 짧고 강렬한 표현)
4. AUTHORITY (전문가·유명인 인용, 권위 부여)
5. URGENCY (시급성·즉시성)

첫 2분 스크립트는 다음 글쓰기 법칙 4가지를 꼭 지켜서 작성해주세요:
1. SHORT & SIMPLE (짧고 간결하게, 핵심 먼저)
2. HOOK & FLOW (후킹 → 자연스러운 흐름)
3. YOU-FOCUSED (독자 중심, 독자의 이익 강조)
4. CREDIBILITY & ACTION (신뢰도 확보 + 행동 유도)

블로그 통합 요약 내용: {blog_summary}

사용자 피드백 분석:
{high_feedback_summary}

{low_feedback_summary}

위 정보들을 토대로 '{keyword}'에 관한 동영상 제목 및 썸네일 이미지 내용 각각 3가지, 스크립트 하나를 생성해주세요.

꼭 아래 포맷대로 생성 해주세요:
[제목]
제목 3가지

[썸네일]
썸네일 3가지

[스크립트]
첫 2분 스크립트 내용
"""
                content = openai_client.chat.completions.create(
                    model=llm_option, 
                    messages=[
                        {"role": "system", "content": "당신은 카피라이팅 법칙을 따라 수집된 데이터에 기반하여 유튜브 동영상 컨텐츠를 만드는 전문가입니다."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.3, 
                    max_tokens=1500, 
                    # n=3, 
                )
                
                result_yt = content.choices[0].message.content.strip()
                
                # 세션 상태에 결과 저장 (화면 표시용)
                st.session_state.generated_content_yt = result_yt
                st.session_state.content_generated_yt = True

                try:
                    print("유튜브 콘텐츠 프롬프트 내용\n\n", prompt)
                    parts = result_yt.split("\n\n")
                    
                    title = ""
                    thumbnail = ""
                    script = ""
                        
                    # 각 부분 추출
                    for i, part in enumerate(parts):
                        if part.startswith("[제목]"):  # 제목 문자열 추출 (첫 줄은 [제목]이므로 제외)
                            title_lines = part.split("\n")[1:]
                            title = "\n".join(title_lines).strip()
                        elif part.startswith("[썸네일]"):  # 썸네일 문자열 추출 (첫 줄은 [썸네일]이므로 제외)
                            thumbnail_lines = part.split("\n")[1:]
                            thumbnail = "\n".join(thumbnail_lines).strip()
                        elif part.startswith("[스크립트]"):  # 스크립트 문자열 추출 (첫 줄은 [스크립트]이므로 제외)
                            script_lines = part.split("\n")[1:]
                            script = "\n".join(script_lines).strip()
                    
                    # 생성한 유튜브 컨텐츠 정보를 DB에 저장
                    try:
                        conn = connect_postgres()
                        cur = conn.cursor()
                            
                        # 생성된 콘텐츠 저장
                        cur.execute("""
                            INSERT INTO content_youtube (blog_id, keyword, title, thumbnail, script)
                            VALUES (%s, %s, %s, %s, %s)
                            """, (blog_id, keyword, title, thumbnail, script))
                        
                        conn.commit()
                        cur.close()
                        conn.close()
                        
                        st.success("유튜브 콘텐츠가 성공적으로 생성되고 저장되었습니다!")

                    except Exception as e:
                        st.error(f"유튜브 콘텐츠 저장 중 오류가 발생했습니다: {str(e)}")
                
                except Exception as e:
                    st.error(f"유튜브 생성된 콘텐츠 파싱 중 오류가 발생했습니다: {str(e)}")
            
            except Exception as e:
                st.error(f"유튜브 콘텐츠 생성 중 오류가 발생했습니다: {str(e)}")
    

    # 인스타 콘텐츠 생성
    if insta_button and blog_id:
        st.session_state.current_blog_id = blog_id

        # 블로그 ID 확인
        conn = connect_postgres()
        cur = conn.cursor()
        
        # blog_int_summary에서 확인
        cur.execute("""SELECT COUNT(*) FROM blog_int_summary WHERE search_unique_id = %s """, (blog_id,))
        blog_exists = cur.fetchone()[0]
        
        if blog_exists == 0:
            st.error(f"입력한 블로그 ID '{blog_id}'를 찾을 수 없습니다.")
        else:
            # 이미 유튜브 콘텐츠가 있는지 확인
            cur.execute("""SELECT COUNT(*) FROM content_instagram WHERE blog_id = %s """, (blog_id,))
            content_exists = cur.fetchone()[0]
            
            if content_exists > 0:
                st.session_state.instagram_status = 'confirm_needed'
            else:
                st.session_state.instagram_status = 'confirmed'
        
        cur.close()
        conn.close()
        st.rerun()
    elif insta_button and not blog_id:
        st.warning("블로그 요약 ID를 입력해주세요.")
        
    # 생성전에 확인
    if st.session_state.instagram_status == 'confirm_needed':
        st.warning(f"아이디 '{st.session_state.current_blog_id}' 블로그 내용으로는 이미 인스타그램 콘텐츠를 생성한 적이 있습니다. 그래도 생성하시겠습니까?")
    
        col1, col2 = st.columns(2)
        with col1:
            if st.button("예", key="confirm_yes_ig"):
                st.session_state.instagram_status = 'confirmed'
                st.rerun()
        with col2:
            if st.button("아니오", key="confirm_no_ig"):
                st.session_state.instagram_status = 'show_existing'
                st.rerun()
        
    if st.session_state.instagram_status == 'show_existing':
        try:
            conn = connect_postgres()
            cur = conn.cursor()

            cur.execute("""
            SELECT blog_id, keyword, pics, caption, hashtags
            FROM content_instagram 
            WHERE blog_id = %s 
            """, (st.session_state.current_blog_id,))

            existing_results = cur.fetchall()
        
            cur.close()
            conn.close()
            
            if existing_results:
                df = pd.DataFrame(existing_results, columns=["블로그 ID", "키워드", "사진", "설명", "태그"])
                st.dataframe(
                    df, 
                    column_config={
                        "블로그 ID": st.column_config.Column(width="small"),
                        "키워드": st.column_config.Column(width="small"),
                        "사진": st.column_config.Column(width="large"),
                        "설명": st.column_config.Column(width="large"),
                        "태그": st.column_config.Column(width="medium")
                    }, 
                    hide_index=True,
                    use_container_width=True
                )

            if st.button("새 콘텐츠 생성", key="new_instagram_content"):
                st.session_state.instagram_status = 'initial'
                st.session_state.current_blog_id = None
                st.rerun()
        
        except Exception as e:
            st.error(f"결과 조회 중 오류가 발생했습니다: {str(e)}")
        
    if st.session_state.instagram_status == 'confirmed' and st.session_state.current_blog_id:
        with st.spinner('인스타그램 콘텐츠를 생성하는 중입니다...'):
            try:
                conn = connect_postgres()
                cur = conn.cursor()
                cur.execute("""SELECT int_summary, keyword FROM blog_int_summary WHERE search_unique_id = %s""", (blog_id,))
                
                result = cur.fetchone()
                cur.close()
                conn.close()
                    
                if result:
                    blog_summary = result[0]
                    keyword = result[1]
                else:
                    st.error(f"ID {blog_id}에 해당하는 블로그 요약을 찾을 수 없습니다.")
                    st.stop()


                prompt = f"""인스타그램 콘텐츠를 작성하고자 합니다.

    인스타그램은 사진과 영상이 핵심인 시각적 플랫폼이지만, 게시글(캡션)과 해시태그 역시 매우 중요합니다. 아래 핵심 요소들을 게시글에 반영해 주세요:

    1. 비주얼 중심의 디자인:
    - 고퀄리티 이미지/영상: 인스타그램은 시각적 플랫폼이므로, 선명하고 매력적인 사진이나 영상을 사용해야 합니다.
    - 통일된 스타일: 필터, 색감, 레이아웃 등에서 일관성을 유지해 브랜드 정체성을 확실히 하세요.

    2. 간결하고 강렬한 캡션:
    - 핵심 메시지 전달: 짧은 문장 안에 핵심 아이디어나 스토리를 담아 독자의 관심을 유도합니다.
    - 후킹 요소 포함: 캡션의 첫 문장으로 질문, 도발적인 문구, 혹은 놀라운 사실을 제시해 관심을 끌어보세요.

    3. 효과적인 해시태그 전략:
    - 관련 해시태그 활용: 콘텐츠와 연관된 인기 해시태그를 선택해 도달 범위를 확장합니다.
    - 브랜딩 해시태그: 고유의 브랜드 해시태그를 만들어 꾸준히 사용하면, 팔로워들이 쉽게 관련 콘텐츠를 찾아볼 수 있습니다.

    4. 대화형 및 참여 유도
    - 콜 투 액션 (CTA): 팔로워에게 댓글을 달거나 질문에 응답하도록 유도하는 문구를 포함하세요.
    - 스토리, 리포스트, DM 활용: 인스타그램의 다양한 기능을 활용해 사용자와의 상호작용을 극대화합니다.

    5. 일관된 톤과 메시지:
    - 브랜드 보이스 유지: 작성하는 모든 캡션과 콘텐츠에서 브랜드의 톤과 메시지를 일관되게 전달해 신뢰도를 높입니다.

    그리고 아래 글쓰기 법칙 4가지를 지켜주세요:
    1. SHORT & SIMPLE (짧고 간결하게, 핵심 먼저)
    2. HOOK & FLOW (후킹 → 자연스러운 흐름)
    3. YOU-FOCUSED (독자 중심, 독자의 이익 강조)
    4. CREDIBILITY & ACTION (신뢰도 확보 + 행동 유도)

    블로그 통합 요약 내용: {blog_summary}

    위 정보들을 토대로, '{keyword}'에 관한 인스타그램 게시물을 아래 포맷에 맞춰 생성해 주세요:
    [사진]

    [게시글]

    [해시 태그]
    """
                content_ig = openai_client.chat.completions.create(
                    model=llm_option, 
                    messages=[
                        {"role": "system", "content": "당신은 카피라이팅 법칙을 따라 수집된 데이터에 기반하여 인스타그램 컨텐츠를 만드는 전문가입니다."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.3,
                    max_tokens=1500
                )
                
                result_ig = content_ig.choices[0].message.content.strip()
                
                # 세션 상태에 결과 저장 (화면 표시용)
                st.session_state.generated_content_ig = result_ig
                st.session_state.content_generated_ig = True

                try:
                    parts = result_ig.split("\n\n")
                    
                    pics = ""
                    caption = ""
                    hashtags = ""
                        
                    # 각 부분 추출
                    for i, part in enumerate(parts):
                        if part.startswith("[사진]"):
                            pics_lines = part.split("\n")[1:]
                            pics = "\n".join(pics_lines).strip()
                        elif part.startswith("[게시글]"):
                            caption_lines = part.split("\n")[1:]
                            caption = "\n".join(caption_lines).strip()
                        elif part.startswith("[해시 태그]"):
                            hashtags_lines = part.split("\n")[1:]
                            hashtags = "\n".join(hashtags_lines).strip()
                    
                    # 생성한 인스타그램 컨텐츠 정보를 DB에 저장
                    try:
                        conn = connect_postgres()
                        cur = conn.cursor()
                            
                        # 생성된 콘텐츠 저장
                        cur.execute("""
                            INSERT INTO content_instagram (blog_id, keyword, pics, caption, hashtags)
                            VALUES (%s, %s, %s, %s, %s)
                            """, (blog_id, keyword, pics, caption, hashtags))
                        
                        conn.commit()
                        cur.close()
                        conn.close()
                        
                        st.success("인스타그램 콘텐츠가 성공적으로 생성되고 저장되었습니다!")

                    except Exception as e:
                        st.error(f"인스타그램 콘텐츠 저장 중 오류가 발생했습니다: {str(e)}")
                
                except Exception as e:
                    st.error(f"생성된 인스타그램 콘텐츠 파싱 중 오류가 발생했습니다: {str(e)}")
            
            except Exception as e:
                st.error(f"인스타그램 콘텐츠 생성 중 오류가 발생했습니다: {str(e)}")
    

    # 쓰레드 콘텐츠 생성
    if thrd_button and blog_id:
        st.session_state.current_blog_id = blog_id

        # 블로그 ID 확인
        conn = connect_postgres()
        cur = conn.cursor()
        
        # blog_int_summary에서 확인
        cur.execute("""SELECT COUNT(*) FROM blog_int_summary WHERE search_unique_id = %s """, (blog_id,))
        blog_exists = cur.fetchone()[0]
        
        if blog_exists == 0:
            st.error(f"입력한 블로그 ID '{blog_id}'를 찾을 수 없습니다.")
        else:
            # 이미 유튜브 콘텐츠가 있는지 확인
            cur.execute("""SELECT COUNT(*) FROM content_threads WHERE blog_id = %s """, (blog_id,))
            content_exists = cur.fetchone()[0]
            
            if content_exists > 0:
                st.session_state.threads_status = 'confirm_needed'
            else:
                st.session_state.threads_status = 'confirmed'
        
        cur.close()
        conn.close()
        st.rerun()
    elif thrd_button and not blog_id:
        st.warning("블로그 요약 ID를 입력해주세요.")
        
    # 생성전에 확인
    if st.session_state.threads_status == 'confirm_needed':
        st.warning(f"아이디 '{st.session_state.current_blog_id}' 블로그 내용으로는 이미 스레드 콘텐츠를 생성한 적이 있습니다. 그래도 생성하시겠습니까?")
    
        col1, col2 = st.columns(2)
        with col1:
            if st.button("예", key="confirm_yes_th"):
                st.session_state.threads_status = 'confirmed'
                st.rerun()
        with col2:
            if st.button("아니오", key="confirm_no_th"):
                st.session_state.threads_status = 'show_existing'
                st.rerun()
        
    if st.session_state.threads_status == 'show_existing':
        try:
            conn = connect_postgres()
            cur = conn.cursor()

            cur.execute("""
            SELECT blog_id, keyword, post, pics, tags
            FROM content_threads 
            WHERE blog_id = %s 
            """, (st.session_state.current_blog_id,))

            existing_results = cur.fetchall()
        
            cur.close()
            conn.close()
            
            if existing_results:
                df = pd.DataFrame(existing_results, columns=["블로그 ID", "키워드", "게시글", "사진", "태그"])
                st.dataframe(
                    df, 
                    column_config={
                        "블로그 ID": st.column_config.Column(width="small"),
                        "키워드": st.column_config.Column(width="small"),
                        "게시글": st.column_config.Column(width="large"),
                        "사진": st.column_config.Column(width="large"),
                        "태그": st.column_config.Column(width="medium")
                    }, 
                    hide_index=True,
                    use_container_width=True
                )

            if st.button("새 콘텐츠 생성", key="new_threads_content"):
                st.session_state.threads_status = 'initial'
                st.session_state.current_blog_id = None
                st.rerun()
        
        except Exception as e:
            st.error(f"결과 조회 중 오류가 발생했습니다: {str(e)}")
    
    if st.session_state.threads_status == 'confirmed' and st.session_state.current_blog_id:
        with st.spinner('스레드 콘텐츠를 생성하는 중입니다...'):
            try:
                conn = connect_postgres()
                cur = conn.cursor()
                cur.execute("""SELECT int_summary, keyword FROM blog_int_summary WHERE search_unique_id = %s""", (blog_id,))
                
                result = cur.fetchone()
                cur.close()
                conn.close()
                    
                if result:
                    blog_summary = result[0]
                    keyword = result[1]
                else:
                    st.error(f"ID {blog_id}에 해당하는 블로그 요약을 찾을 수 없습니다.")
                    st.stop()

                prompt = f"""새로 만들 Threads 콘텐츠를 작성하고자 합니다.

게시글에는 다음과 같은 핵심 요소가 반드시 포함되어야 합니다:
1. 핵심 메시지와 목적 명확화: 짧은 글로 강렬한 핵심 메시지를 전달하기 위해, 콘텐츠의 주제와 목표를 사전에 명확히 설정할 것
2. 후킹(Hook) 요소: 첫 문장은 독자의 관심을 즉시 끌어야 하므로, 질문, 도발적인 주장 또는 놀라운 사실 등으로 시작하여 관심을 유도할 것
3. 간결함과 명료함: 제한된 글자수 내에서 불필요한 단어를 제거하고 핵심 메시지만 담도록 작성할 것
4. 비주얼 요소의 활용: 이미지나 이모지 등 시각적 요소를 적절히 사용하여 눈길을 끌고 메시지를 보완할 것
5. 일관된 톤과 브랜딩: 브랜드나 개인의 목소리를 일관되게 유지해 팔로워들이 콘텐츠를 쉽게 인식할 수 있도록 할 것
6. 대화형 접근: 소통과 댓글을 통한 상호작용이 중요하므로, 독자에게 질문을 던지거나 의견을 유도하는 콜 투 액션(CTA)을 포함할 것

아래 글쓰기 법칙 4가지를 지켜주세요:
1. SHORT & SIMPLE (짧고 간결하게, 핵심 먼저)
2. HOOK & FLOW (후킹 → 자연스러운 흐름)
3. YOU-FOCUSED (독자 중심, 독자의 이익 강조)
4. CREDIBILITY & ACTION (신뢰도 확보 + 행동 유도)

추가로, 게시글을 돋보이게 할 수 있는 이미지 내용과 태그도 추천해주세요. 이건 선택사항입니다.

블로그 통합 요약 내용: {blog_summary}

위 정보들을 토대로, '{keyword}'에 관한 Threads 게시물을 아래 포맷에 맞춰 생성해주세요:
[게시글]

[사진]

[태그]
"""
                
                content_th = openai_client.chat.completions.create(
                    model=llm_option, 
                    messages=[
                        {"role": "system", "content": "당신은 수집된 데이터에 기반하여 쓰레드 콘텐츠를 만드는 전문가입니다."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.3,
                    max_tokens=1500
                )
                
                result_th = content_th.choices[0].message.content.strip()
                
                # 세션 상태에 결과 저장 (화면 표시용)
                st.session_state.generated_content_th = result_th
                st.session_state.content_generated_th = True

                try:
                    parts = result_th.split("\n\n")
                    
                    post = ""
                    pics = ""
                    tags = ""
                        
                    # 각 부분 추출
                    for i, part in enumerate(parts):
                        if part.startswith("[게시글]"):  # 제목 문자열 추출 (첫 줄은 [게시글]이므로 제외)
                            post_lines = part.split("\n")[1:]
                            post = "\n".join(post_lines).strip()
                        elif part.startswith("[사진]"):  # 썸네일 문자열 추출 (첫 줄은 [사진]이므로 제외)
                            pics_lines = part.split("\n")[1:]
                            pics = "\n".join(pics_lines).strip()
                        elif part.startswith("[태그]"):  # 스크립트 문자열 추출 (첫 줄은 [태그]이므로 제외)
                            tags_lines = part.split("\n")[1:]
                            tags = "\n".join(tags_lines).strip()
                    
                    # 생성한 쓰레드 컨텐츠 정보를 DB에 저장
                    try:
                        conn = connect_postgres()
                        cur = conn.cursor()
                        
                        # 생성된 콘텐츠 저장
                        cur.execute("""
                            INSERT INTO content_threads (blog_id, keyword, post, pics, tags)
                            VALUES (%s, %s, %s, %s, %s)
                            """, (blog_id, keyword, post, pics, tags))
                        
                        conn.commit()
                        cur.close()
                        conn.close()
                        
                        st.success("콘텐츠가 성공적으로 생성되고 저장되었습니다!")

                    except Exception as e:
                        st.error(f"콘텐츠 저장 중 오류가 발생했습니다: {str(e)}")
                
                except Exception as e:
                    st.error(f"생성된 콘텐츠 파싱 중 오류가 발생했습니다: {str(e)}")
            except Exception as e:
                st.error(f"콘텐츠 생성 중 오류가 발생했습니다: {str(e)}")
    
    
    # 생성된 유튜브 콘텐츠 표시
    if st.session_state.get('content_generated_yt', False):
        st.subheader("생성된 유튜브 콘텐츠")
        
        # 원본 텍스트 (접을 수 있게)
        with st.expander("원본 생성 텍스트 보기", expanded=True):
            if hasattr(st.session_state, 'generated_content_yt'):
                st.text(st.session_state.generated_content_yt)
    
    # 생성된 인스타그램 콘텐츠 표시
    if st.session_state.get('content_generated_ig', False):
        st.subheader("생성된 인스타그램 콘텐츠")
        
        # 원본 텍스트 (접을 수 있게)
        with st.expander("원본 생성 텍스트 보기", expanded=True):
            if hasattr(st.session_state, 'generated_content_ig'):
                st.text(st.session_state.generated_content_ig)
    
    # 생성된 쓰레드 콘텐츠 표시
    if st.session_state.get('content_generated_th', False):
        st.subheader("생성된 쓰레드 콘텐츠")
        
        # 원본 텍스트 (접을 수 있게)
        with st.expander("원본 생성 텍스트 보기", expanded=True):
            if hasattr(st.session_state, 'generated_content_th'):
                st.text(st.session_state.generated_content_th)
    
    st.markdown("---")
    
    st.subheader("콘텐츠 평가하기")
    
    # 유튜브 콘텐츠 평가
    with st.expander("유튜브 콘텐츠 품질 평가"):
        st.subheader("유튜브 콘텐츠 품질 평가")
        try:
            conn = connect_postgres()
            cur = conn.cursor()
            
            # 모든 유튜브 콘텐츠 조회
            cur.execute("""
            SELECT id, keyword, title, thumbnail, script 
            FROM content_youtube 
            ORDER BY created_at DESC
            """)
            
            youtube_contents = cur.fetchall()
            cur.close()
            conn.close()
            
            if youtube_contents:
                df_contents = pd.DataFrame(youtube_contents, columns=['ID', '키워드', '제목', '썸네일', '스크립트'])
                st.dataframe(
                    df_contents,
                    column_config={
                        "ID": st.column_config.Column(width="small"),
                        "키워드": st.column_config.Column(width="small"),
                        "제목": st.column_config.Column(width="large"),
                        "썸네일": st.column_config.Column(width="large"),
                        "스크립트": st.column_config.Column(width="large"),
                    },
                    hide_index=True,
                    use_container_width=True
                )
            else:
                st.info("평가할 유튜브 콘텐츠가 없습니다. 먼저 콘텐츠를 생성해주세요.")
                
        except Exception as e:
            st.error(f"유튜브 콘텐츠 목록을 불러오는 중 오류가 발생했습니다: {str(e)}")

        content_id_yt = st.number_input("평가할 콘텐츠 ID를 입력하세요", min_value=1, step=1)
        feedback_score_yt = st.slider("이 유튜브 콘텐츠의 품질을 평가해주세요", 1, 10, 7)
        feedback_text_yt = st.text_area("이 유튜브 콘텐츠의 개선 사항이나 추가 의견이 있다면 알려주세요")
        submit_feedback_yt = st.button("피드백 제출", key="submit_youtube")

        if submit_feedback_yt and feedback_score_yt and feedback_text_yt and content_id_yt:
            # 데이터베이스에서 해당 ID의 콘텐츠 정보 가져오기
            try:
                conn = connect_postgres()
                cur = conn.cursor()
                
                cur.execute("""
                SELECT title, thumbnail, script
                FROM content_youtube
                WHERE id = %s
                """, (content_id_yt,))
                
                content_info = cur.fetchone()
                cur.close()
                conn.close()
                
                if content_info:
                    title, thumbnail, script = content_info
                    
                    # 피드백 저장
                    success = save_feedback_yt(content_id_yt, title, thumbnail, script, feedback_score_yt, feedback_text_yt, "YouTube")

                    if success:
                        st.success(f"콘텐츠 ID {content_id_yt}에 대한 피드백이 성공적으로 저장되었습니다!")
                else:
                    st.error(f"ID {content_id_yt}에 해당하는 콘텐츠를 찾을 수 없습니다.")
                    
            except Exception as e:
                st.error(f"콘텐츠 정보를 가져오는 중 오류가 발생했습니다: {str(e)}")
        elif submit_feedback_yt:
            if not content_id_yt:
                st.warning("평가할 콘텐츠 ID를 입력해주세요.")
            elif not feedback_text_yt:
                st.warning("개선 사항이나 의견을 입력해주세요.")

    # 인스타그램 콘텐츠 평가
    with st.expander("인스타그램 콘텐츠 품질 평가"):
        st.subheader("인스타그램 콘텐츠 품질 평가")
        
        # 모든 인스타그램 콘텐츠 조회
        try:
            conn = connect_postgres()
            cur = conn.cursor()
            
            # 모든 인스타그램 콘텐츠 조회
            cur.execute("""
            SELECT id, keyword, pics, caption, hashtags 
            FROM content_instagram 
            ORDER BY created_at DESC
            """)
            
            instagram_contents = cur.fetchall()
            cur.close()
            conn.close()
            
            if instagram_contents:
                df_contents = pd.DataFrame(instagram_contents, columns=['ID', '키워드', '사진', '설명', '해시 태그'])
                st.dataframe(
                    df_contents,
                    column_config={
                        "ID": st.column_config.Column(width="small"),
                        "키워드": st.column_config.Column(width="small"),
                        "사진": st.column_config.Column(width="large"),
                        "설명": st.column_config.Column(width="large"),
                        "해시 태그": st.column_config.Column(width="large"),
                    },
                    hide_index=True,
                    use_container_width=True
                )
            else:
                st.info("평가할 인스타그램 콘텐츠가 없습니다. 먼저 콘텐츠를 생성해주세요.")
                
        except Exception as e:
            st.error(f"인스타그램 콘텐츠 목록을 불러오는 중 오류가 발생했습니다: {str(e)}")
        
        content_id_ig = st.number_input("평가할 인스타 콘텐츠 ID를 입력하세요", min_value=1, step=1)
        feedback_score_ig = st.slider("이 인스타그램 콘텐츠의 품질을 평가해주세요", 1, 10, 7)
        feedback_text_ig = st.text_area("이 인스타그램 개선 사항이나 추가 의견이 있다면 알려주세요")
        submit_feedback_ig = st.button("인스타 피드백 제출", key="submit_instagram")
        
        if submit_feedback_ig and feedback_score_ig and feedback_text_ig and content_id_ig:
            # 데이터베이스에서 해당 ID의 콘텐츠 정보 가져오기
            try:
                conn = connect_postgres()
                cur = conn.cursor()
                
                cur.execute("""
                SELECT pics, caption, hashtags
                FROM content_instagram
                WHERE id = %s
                """, (content_id_ig,))
                
                content_info = cur.fetchone()
                cur.close()
                conn.close()
                
                if content_info:
                    pics, caption, hashtags = content_info
                    
                    # 피드백 저장
                    success = save_feedback_ig(content_id_ig, pics, caption, hashtags, feedback_score_ig, feedback_text_ig)

                    if success:
                        st.success(f"콘텐츠 ID {content_id_ig}에 대한 인스타그램 피드백이 성공적으로 저장되었습니다!")
                else:
                    st.error(f"ID {content_id_ig}에 해당하는 인스타그램 콘텐츠를 찾을 수 없습니다.")
                    
            except Exception as e:
                st.error(f"콘텐츠 정보를 가져오는 중 오류가 발생했습니다: {str(e)}")
        elif submit_feedback_ig:
            if not content_id_ig:
                st.warning("평가할 인스타그램 콘텐츠 ID를 입력해주세요.")
            elif not feedback_text_ig:
                st.warning("개선 사항이나 의견을 입력해주세요.")
    
    # 쓰레드 콘텐츠 평가
    with st.expander("스레드 콘텐츠 품질 평가"):
        st.subheader("스레드 콘텐츠 품질 평가")
        try:
            conn = connect_postgres()
            cur = conn.cursor()
            
            # 모든 인스타그램 콘텐츠 조회
            cur.execute("""
            SELECT id, keyword, post, pics, tags 
            FROM content_threads
            ORDER BY created_at DESC
            """)
            
            threads_contents = cur.fetchall()
            cur.close()
            conn.close()
            
            if threads_contents:
                df_contents = pd.DataFrame(threads_contents, columns=['ID', '키워드', '게시글', '사진', '태그'])
                st.dataframe(
                    df_contents,
                    column_config={
                        "ID": st.column_config.Column(width="small"),
                        "키워드": st.column_config.Column(width="small"),
                        "게시글": st.column_config.Column(width="large"),
                        "사진": st.column_config.Column(width="large"),
                        "태그": st.column_config.Column(width="large"),
                    },
                    hide_index=True,
                    use_container_width=True
                )
            else:
                st.info("평가할 스레드 콘텐츠가 없습니다. 먼저 콘텐츠를 생성해주세요.")
                
        except Exception as e:
            st.error(f"스레드 콘텐츠 목록을 불러오는 중 오류가 발생했습니다: {str(e)}")
        
        content_id_th = st.number_input("평가할 스레드 콘텐츠 ID를 입력하세요", min_value=1, step=1)
        feedback_score_th = st.slider("이 스레드 콘텐츠의 품질을 평가해주세요", 1, 10, 7)
        feedback_text_th = st.text_area("이 스레드 콘텐츠의 개선 사항이나 추가 의견이 있다면 알려주세요")
        submit_feedback_th = st.button("스레드 피드백 제출", key="submit_threads")

        if submit_feedback_th and feedback_score_th and feedback_text_th and content_id_th:
            # 데이터베이스에서 해당 ID의 콘텐츠 정보 가져오기
            try:
                conn = connect_postgres()
                cur = conn.cursor()
                
                cur.execute("""
                SELECT post, pics, tags
                FROM content_threads
                WHERE id = %s
                """, (content_id_th,))
                
                content_info = cur.fetchone()
                cur.close()
                conn.close()
                
                if content_info:
                    post, pics, tags = content_info
                    
                    # 피드백 저장
                    success = save_feedback_th(content_id_th, post, pics, tags, feedback_score_th, feedback_text_th)

                    if success:
                        st.success(f"콘텐츠 ID {content_id_th}에 대한 쓰레드 피드백이 성공적으로 저장되었습니다!")
                else:
                    st.error(f"ID {content_id_th}에 해당하는 쓰레드 콘텐츠를 찾을 수 없습니다.")
                    
            except Exception as e:
                st.error(f"콘텐츠 정보를 가져오는 중 오류가 발생했습니다: {str(e)}")
        elif submit_feedback_th:
            if not content_id_th:
                st.warning("평가할 쓰레드 콘텐츠 ID를 입력해주세요.")
            elif not feedback_text_th:
                st.warning("개선 사항이나 의견을 입력해주세요.")

# 분석 내용 확인하기 탭
with tab_result:
    st.info("지금까지 분석한 썸네일 이미지에 대한 정보를 조회하는 공간입니다.")
    try:
        st.subheader("썸네일 분석 결과")
        
        conn = connect_postgres()
        cur = conn.cursor()

        cur.execute("""
        SELECT 
            video_thumbnail, search_unique_id, keyword, channel_url, channel_name, video_id, video_title, is_shorts, thumbnail_result
        FROM 
            analysis_thumbnail
        ORDER BY
            created_at DESC
        """)

        thumbnail_results = cur.fetchall()
        
        cur.close()
        conn.close()

        # 키워드도 조회해야
        columns = ['썸네일', '검색ID', '키워드', '채널URL', '채널명', 'video_id', '제목', '쇼츠', '썸네일 분석']
        
        thumbnail_df = pd.DataFrame(thumbnail_results, columns=columns)
        st.dataframe(
            thumbnail_df,
            column_config={
                "썸네일": st.column_config.ImageColumn(width="medium"),
                "채널명": st.column_config.Column(width="small"),
                "제목": st.column_config.Column(width="medium"),
                "썸네일 분석": st.column_config.TextColumn(width="large"),
                "생성일시": st.column_config.DatetimeColumn(format="YYYY-MM-DD HH:mm")
            },
            hide_index=True,
            use_container_width=True
        )
    
    except Exception as e:
        st.error(f"데이터 조회 중 오류가 발생했습니다: {str(e)}")
