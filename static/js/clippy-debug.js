/**
 * Clippy Debug Functions
 * Utilities for testing and debugging Clippy behavior
 */

(function() {
    'use strict';
    
    const SESSION_KEY = 'clippy_session_time';
    const SESSION_DURATION = 3 * 60 * 60 * 1000; // 3 hours in milliseconds
    
    /**
     * Debug: Check current session state
     * Usage: debugClippySession()
     */
    window.debugClippySession = function() {
        const lastActivity = localStorage.getItem(SESSION_KEY);
        const now = Date.now();
        
        if (!lastActivity) {
            console.log('Session state: NEW (no timestamp found)');
            return { isNew: true, timestamp: null, timeSince: null };
        }
        
        const lastActivityTime = parseInt(lastActivity, 10);
        const timeSince = now - lastActivityTime;
        const hoursSince = (timeSince / (60 * 60 * 1000)).toFixed(2);
        const isExpired = timeSince >= SESSION_DURATION;
        
        console.log('Session state:', isExpired ? 'EXPIRED (new session)' : 'ACTIVE');
        console.log('Timestamp:', new Date(lastActivityTime).toLocaleString());
        console.log('Time since:', hoursSince, 'hours');
        console.log('Time until expiration:', ((SESSION_DURATION - timeSince) / (60 * 60 * 1000)).toFixed(2), 'hours');
        
        return {
            isNew: isExpired,
            timestamp: lastActivityTime,
            timeSince: timeSince,
            hoursSince: hoursSince
        };
    };
    
    /**
     * Debug: Force reset session (clear and refresh)
     * Usage: resetClippySessionDebug()
     */
    window.resetClippySessionDebug = function() {
        localStorage.removeItem(SESSION_KEY);
        console.log('Session cleared! Refreshing page in 1 second...');
        setTimeout(function() {
            location.reload();
        }, 1000);
    };
    
    /**
     * Debug: Set session to expired (3+ hours ago)
     * Usage: expireClippySession()
     */
    window.expireClippySession = function() {
        const expiredTime = Date.now() - SESSION_DURATION - 1000; // 1 second past expiration
        localStorage.setItem(SESSION_KEY, expiredTime.toString());
        console.log('Session set to expired. Refreshing page in 1 second...');
        setTimeout(function() {
            location.reload();
        }, 1000);
    };
    
    /**
     * Debug: Set session to active (just now)
     * Usage: setActiveClippySession()
     */
    window.setActiveClippySession = function() {
        localStorage.setItem(SESSION_KEY, Date.now().toString());
        console.log('Session set to active. Refreshing page in 1 second...');
        setTimeout(function() {
            location.reload();
        }, 1000);
    };
    
    /**
     * Debug: Test a phrase with placeholders
     * Usage: testClippyPhrase("Today is {weekday} at {time}!")
     * @param {string} phrase - Phrase to test (can include placeholders like {weekday}, {date}, etc.)
     */
    window.testClippyPhrase = function(phrase) {
        if (!window.currentClippyAgent) {
            console.error('Clippy agent not loaded yet. Please wait for the agent to initialize.');
            return;
        }
        
        if (!phrase || typeof phrase !== 'string') {
            console.error('Please provide a valid phrase string.');
            console.log('Example: testClippyPhrase("Today is {weekday} at {time}!")');
            console.log('Available placeholders: {weekday}, {date}, {time}, {month}, {year}, {day}');
            return;
        }
        
        // Use the exposed processPhrasePlaceholders function from clippy-phrases.js
        const processedPhrase = window.processPhrasePlaceholders(phrase);
        
        console.log('Original phrase:', phrase);
        console.log('Processed phrase:', processedPhrase);
        
        window.currentClippyAgent.speak(processedPhrase);
    };
})();

