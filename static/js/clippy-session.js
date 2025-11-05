/**
 * Clippy Session Management
 * Tracks 3-hour browser sessions using localStorage
 */

(function() {
    'use strict';
    
    const SESSION_KEY = 'clippy_session_time';
    const SESSION_DURATION = 3 * 60 * 60 * 1000; // 3 hours in milliseconds
    
    /**
     * Get current session state
     * @returns {Object} {isNewSession: boolean, isActive: boolean}
     */
    window.getClippySessionState = function() {
        const lastActivity = localStorage.getItem(SESSION_KEY);
        const now = Date.now();
        
        if (!lastActivity) {
            // First time or cleared storage - new session
            localStorage.setItem(SESSION_KEY, now.toString());
            return { isNewSession: true, isActive: true };
        }
        
        const lastActivityTime = parseInt(lastActivity, 10);
        const timeSinceLastActivity = now - lastActivityTime;
        
        if (timeSinceLastActivity >= SESSION_DURATION) {
            // Session expired - start new session
            localStorage.setItem(SESSION_KEY, now.toString());
            return { isNewSession: true, isActive: true };
        }
        
        // Active session - update last activity time
        localStorage.setItem(SESSION_KEY, now.toString());
        return { isNewSession: false, isActive: true };
    };
    
    /**
     * Reset session timer (call on page navigation)
     */
    window.resetClippySession = function() {
        localStorage.setItem(SESSION_KEY, Date.now().toString());
    };
    
    /**
     * Initialize Clippy with session awareness
     * @param {string} agentName - Name of the Clippy agent
     * @param {Object} options - Options object
     * @param {boolean} options.useAnimations - Force animations (e.g., Settings page agent switching)
     * @param {Function} options.onLoad - Callback when agent loads
     * @param {Function} options.onNewSession - Callback on new session
     */
    window.initClippyWithSession = function(agentName, options) {
        options = options || {};
        const useAnimations = options.useAnimations || false;
        const onLoad = options.onLoad || function() {};
        const onNewSession = options.onNewSession || function() {};
        
        // Check session state FIRST (before resetting timer)
        // Note: getClippySessionState() already updates the timestamp
        const sessionState = getClippySessionState();
        const isNewSession = sessionState.isNewSession;
        
        // Determine if we should use animations
        const shouldAnimate = useAnimations || isNewSession;
        
        // Load the agent
        clippy.load(agentName, function(agent) {
            // Show agent (with or without animation based on session state)
            if (shouldAnimate) {
                // New session or forced animations - use full animation
                agent.show();
            } else {
                // Active session - instant show using fast mode
                // But we need to ensure positioning happens first (fast mode skips positioning)
                agent._hidden = false;
                var top = agent._el.css('top');
                var left = agent._el.css('left');
                if (top === 'auto' || !top || left === 'auto' || !left) {
                    // Position the element if not already positioned
                    var margin = 16;
                    var w = $(window).width();
                    var h = $(window).height();
                    var elW = agent._el.outerWidth ? agent._el.outerWidth() : 0;
                    var elH = agent._el.outerHeight ? agent._el.outerHeight() : 0;
                    var leftPos = Math.max(margin, w - elW - margin);
                    var topPos = Math.max(margin, h - elH - margin);
                    agent._el.css({ top: topPos, left: leftPos });
                }
                agent._el.show();
                agent.resume();
                agent._onQueueEmpty();
            }
            
            // On new session, speak the welcome message
            if (isNewSession) {
                setTimeout(function() {
                    agent.speak('If you need help, just ask! Or don\'t. I\'ll still be here. Watching.');
                }, shouldAnimate ? 500 : 100); // Small delay to let show animation complete if used
            }
            
            // Call custom onLoad callback
            if (onLoad) {
                onLoad(agent, sessionState);
            }
            
            // Call onNewSession callback if applicable
            if (isNewSession && onNewSession) {
                onNewSession(agent);
            }
        });
    };
    
    // Debug helper functions
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
})();

