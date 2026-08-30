from __future__ import annotations

import json
from datetime import datetime, timezone

import streamlit as st

from edgedash.config import load_config
from edgedash.storage_factory import get_storage_module, init_db
import sqlite3


def main() -> None:
    st.set_page_config(
        page_title="EdgeDash Dashboard",
        page_icon="🎯",
        layout="wide",
        initial_sidebar_state="expanded"
    )
    
    st.title("🎯 EdgeDash Career Intelligence Dashboard")
    st.markdown("Read-only view of your job market intelligence data")
    
    # Load config
    try:
        config = load_config()
    except Exception as exc:
        st.error(f"Failed to load config: {exc}")
        return
    
    # Initialize database using storage factory
    try:
        storage = get_storage_module(config)
        init_db(config)
    except Exception as exc:
        st.error(f"Failed to initialize database: {exc}")
        return
    
    # Sidebar
    with st.sidebar:
        st.header("⚙️ Configuration")
        st.write(f"**Role:** {config.target_role}")
        st.write(f"**Location:** {config.target_city}")
        st.write(f"**Seniority:** {config.target_seniority}")
        st.write(f"**Min Score:** {config.min_fit_score}")
        
        st.divider()
        
        # Stats
        try:
            stats = storage.get_stats()
            st.header("📈 Statistics")
            col1, col2 = st.columns(2)
            with col1:
                st.metric("Total Listings", stats['total_listings'])
                st.metric("Scored", stats['scored_listings'])
                st.metric("Unscored", stats['unscored_listings'])
            with col2:
                st.metric("Skill Gaps", stats['total_gaps'])
                st.metric("Total Cycles", stats['total_cycles'])
            if stats['last_fetch']:
                st.write(f"**Last Fetch:** {stats['last_fetch']}")
            if stats['last_cycle']:
                st.write(f"**Last Cycle:** {stats['last_cycle']}")
        except Exception as exc:
            st.warning(f"Could not load statistics: {exc}")
        
        st.divider()
        
        # Filters
        st.header("🔍 Filters")
        min_score = st.slider(
            "Minimum Fit Score",
            min_value=0,
            max_value=100,
            value=config.min_fit_score,
            step=5
        )
        
        limit = st.slider(
            "Results Limit",
            min_value=5,
            max_value=100,
            value=25,
            step=5
        )
        
        st.divider()
        
        # Refresh button
        if st.button("🔄 Refresh Data"):
            st.rerun()
    
    # Main content tabs
    tab1, tab2, tab3 = st.tabs(["📋 Job Matches", "📊 Skill Gaps", "📜 Cycle History"])
    
    with tab1:
        st.header("Top Job Matches")
        
        listings = storage.get_listings(limit, min_score)
        
        if not listings:
            st.info("No job listings match your criteria. Run a cycle to fetch data.")
        else:
            st.write(f"Found {len(listings)} listings with score ≥ {min_score}")
            
            for listing in listings:
                with st.expander(
                    f"**{listing['title']}** at {listing['company']} — Score: {listing['fit_score']}"
                ):
                    col1, col2 = st.columns(2)
                    
                    with col1:
                        st.write(f"**Location:** {listing['location']}")
                        st.write(f"**Source:** {listing['source']}")
                        st.write(f"**Posted:** {listing['posted_at'] or 'Unknown'}")
                    
                    with col2:
                        st.write(f"**URL:** [{listing['url']}]({listing['url']})")
                        st.write(f"**Fetched:** {listing['fetched_at']}")
                    
                    st.divider()
                    st.write("**Fit Reason:**")
                    st.write(listing.get('fit_reason', 'No reason available'))
                    
                    # Show score components if available
                    components = listing.get('fit_components')
                    if components:
                        if isinstance(components, str):
                            try:
                                components = json.loads(components)
                            except:
                                components = {}
                        
                        if isinstance(components, dict) and components:
                            st.write("**Score Components:**")
                            for key, value in components.items():
                                if key != 'gaps':  # Don't show gaps in main view
                                    st.write(f"- {key}: {value}")
                    
                    # Show description (collapsible)
                    with st.expander("📄 Job Description"):
                        st.write(listing.get('description', 'No description available'))
    
    with tab2:
        st.header("Skill Gap Analysis")
        
        skill_gaps = storage.get_skill_gaps()
        
        if not skill_gaps:
            st.info("No skill gap data available. Run cycles with scoring to populate this data.")
        else:
            st.write(f"Tracking {len(skill_gaps)} skill gaps")
            
            # Display as a table
            for i, gap in enumerate(skill_gaps):
                with st.expander(
                    f"**{gap['skill']}** — Frequency: {gap['frequency']} — Last seen: {gap['last_seen']}"
                ):
                    st.write(f"**Skill:** {gap['skill']}")
                    st.write(f"**Frequency:** {gap['frequency']} listings require this skill")
                    st.write(f"**Last Seen:** {gap['last_seen']}")
    
    with tab3:
        st.header("Cycle History")
        
        # Get cycle log from storage
        try:
            cycle_log = storage.get_cycle_log(50)
            
            if not cycle_log:
                st.info("No cycle history available. Run cycles to populate this data.")
            else:
                st.write(f"Showing last {len(cycle_log)} cycle entries")
                
                for row in cycle_log:
                    status_color = "🟢" if row['status'] == 'ok' else "🔴"
                    
                    with st.expander(
                        f"{status_color} **{row['agent']}** — {row['started_at']} — Status: {row['status'].upper()}"
                    ):
                        col1, col2, col3 = st.columns(3)
                        
                        with col1:
                            st.write(f"**Started:** {row['started_at']}")
                            st.write(f"**Finished:** {row['finished_at']}")
                        
                        with col2:
                            st.write(f"**Records:** {row['records_touched']}")
                            st.write(f"**Status:** {row['status']}")
                        
                        with col3:
                            st.write(f"**Notes:** {row['notes']}")
        except Exception as exc:
            st.error(f"Failed to load cycle history: {exc}")


if __name__ == "__main__":
    main()
