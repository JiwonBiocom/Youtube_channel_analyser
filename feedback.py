import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
import streamlit as st


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

# 피드백 저장 함수를 수정
def save_feedback_yt(search_unique_id, title, thumbnail, script, score, feedback_text, platform):
    try:
        conn = connect_postgres()
        cur = conn.cursor()
        
        # 피드백 테이블 생성 (없는 경우)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS feedback_yt (
            id SERIAL PRIMARY KEY,
            search_unique_id INTEGER NOT NULL,
            title TEXT NOT NULL, 
            thumbnail TEXT NOT NULL,
            script TEXT NOT NULL,
            score INTEGER NOT NULL, 
            feedback TEXT NOT NULL,
            platform VARCHAR(15), 
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        
        # 피드백 저장
        cur.execute("""
        INSERT INTO feedback_yt (search_unique_id, title, thumbnail, script, score, feedback, platform)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (search_unique_id, title, thumbnail, script, score, feedback_text, platform))
        
        conn.commit()
        cur.close()
        conn.close()
        
        return True
    except Exception as e:
        st.error(f"피드백 저장 중 오류가 발생했습니다: {str(e)}")
        return False

def save_feedback_ig(search_unique_id, pics, caption, hashtags, score, feedback_text):
    try:
        conn = connect_postgres()
        cur = conn.cursor()
        
        # 피드백 테이블 생성 (없는 경우)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS feedback_ig (
            id SERIAL PRIMARY KEY,
            search_unique_id INTEGER NOT NULL,
            pics TEXT NOT NULL, 
            caption TEXT NOT NULL,
            hashtags TEXT NOT NULL,
            score INTEGER NOT NULL, 
            feedback TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        
        # 피드백 저장
        cur.execute("""
        INSERT INTO feedback_ig (search_unique_id, pics, caption, hashtags, score, feedback)
        VALUES (%s, %s, %s, %s, %s, %s)
        """, (search_unique_id, pics, caption, hashtags, score, feedback_text))
        
        conn.commit()
        cur.close()
        conn.close()
        
        return True
    except Exception as e:
        st.error(f"피드백 저장 중 오류가 발생했습니다: {str(e)}")
        return False

def save_feedback_th(search_unique_id, post, pics, tags, score, feedback_text):
    try:
        conn = connect_postgres()
        cur = conn.cursor()
        
        # 피드백 테이블 생성 (없는 경우)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS feedback_th (
            id SERIAL PRIMARY KEY,
            search_unique_id INTEGER NOT NULL,
            post TEXT NOT NULL, 
            pics TEXT NOT NULL,
            tags TEXT NOT NULL,
            score INTEGER NOT NULL, 
            feedback TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        
        # 피드백 저장
        cur.execute("""
        INSERT INTO feedback_th (search_unique_id, post, pics, tags, score, feedback)
        VALUES (%s, %s, %s, %s, %s, %s)
        """, (search_unique_id, post, pics, tags, score, feedback_text))
        
        conn.commit()
        cur.close()
        conn.close()
        
        return True
    except Exception as e:
        st.error(f"피드백 저장 중 오류가 발생했습니다: {str(e)}")
        return False
