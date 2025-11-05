/**
 * Clippy Session Management
 * Tracks 3-hour browser sessions using localStorage
 */

(function() {
    'use strict';
    
    const SESSION_KEY = 'clippy_session_time';
    const SESSION_DURATION = 3 * 60 * 60 * 1000; // 3 hours in milliseconds
    const ANIMATION_STATE_KEY = 'clippy_animation_state';
    const SPEECH_TEXT_KEY = 'clippy_speech_text';
    const PAGE_SPECIFIC_ANIMS = ['Searching', 'CheckingSomething']; // Animations to drop on navigation
    
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
     * Hook into agent's speak function to save full text
     * @param {Object} agent - Clippy agent instance
     */
    window.hookClippySpeech = function(agent) {
        if (!agent) return;
        
        // Store original speak function
        const originalSpeak = agent.speak;
        
        // Wrap speak function to save full text
        agent.speak = function(text, hold) {
            // Save the full text to storage when speech starts
            if (text) {
                sessionStorage.setItem(SPEECH_TEXT_KEY, JSON.stringify({
                    text: text,
                    hold: hold || false,
                    timestamp: Date.now()
                }));
            }
            
            // Call original speak function
            const result = originalSpeak.call(this, text, hold);
            
            // Monitor when speech completes by checking balloon state
            // Wait a bit for _active to be set (it happens in _sayWords)
            setTimeout(function() {
                if (!agent || !agent._balloon) return;
                
                let lastActiveState = agent._balloon._active;
                const checkInterval = setInterval(function() {
                    if (!agent || !agent._balloon) {
                        clearInterval(checkInterval);
                        return;
                    }
                    
                    const currentActive = agent._balloon._active;
                    // If it was active and now it's not, speech completed
                    if (lastActiveState && !currentActive) {
                        // Speech completed, clear stored text
                        sessionStorage.removeItem(SPEECH_TEXT_KEY);
                        clearInterval(checkInterval);
                    }
                    lastActiveState = currentActive;
                }, 100);
                
                // Clear interval after reasonable timeout (e.g., 60 seconds for long messages)
                setTimeout(function() {
                    clearInterval(checkInterval);
                }, 60000);
            }, 50);
            
            return result;
        };
    };
    
    /**
     * Save animation state before page unload
     * @param {Object} agent - Clippy agent instance
     */
    window.saveClippyAnimationState = function(agent) {
        if (!agent || !agent._animator) return;
        
        const animator = agent._animator;
        const balloon = agent._balloon;
        const currentAnim = animator.currentAnimationName;
        
        // Skip page-specific animations - don't save them
        if (currentAnim && PAGE_SPECIFIC_ANIMS.indexOf(currentAnim) !== -1) {
            sessionStorage.removeItem(ANIMATION_STATE_KEY);
            sessionStorage.removeItem(SPEECH_TEXT_KEY);
            return;
        }
        
        // Only save if there's an active animation
        if (!currentAnim) {
            sessionStorage.removeItem(ANIMATION_STATE_KEY);
            // Don't remove speech text here - it might still be valid
            return;
        }
        
        // Check if there's stored speech text
        const savedSpeech = sessionStorage.getItem(SPEECH_TEXT_KEY);
        let speechData = null;
        if (savedSpeech) {
            try {
                speechData = JSON.parse(savedSpeech);
                // Only use speech if it was saved recently (within 30 seconds)
                if (Date.now() - speechData.timestamp > 30000) {
                    speechData = null;
                    sessionStorage.removeItem(SPEECH_TEXT_KEY);
                }
            } catch (e) {
                speechData = null;
            }
        }
        
        const state = {
            animation: currentAnim,
            frameIndex: animator._currentFrameIndex || 0,
            timestamp: Date.now(),
            balloonActive: balloon && balloon._active && speechData ? true : false,
            balloonText: speechData ? speechData.text : null,
            balloonHold: speechData ? speechData.hold : false
        };
        
        sessionStorage.setItem(ANIMATION_STATE_KEY, JSON.stringify(state));
    };
    
    /**
     * Restore animation from saved state
     * @param {Object} agent - Clippy agent instance
     * @param {Object} savedState - Saved animation state
     */
    window.restoreClippyAnimation = function(agent, savedState) {
        if (!agent || !savedState || !savedState.animation) return;
        
        if (!agent.hasAnimation(savedState.animation)) {
            console.warn('Animation not available:', savedState.animation);
            return;
        }
        
        const animator = agent._animator;
        const animation = animator._data.animations[savedState.animation];
        
        if (!animation || !animation.frames) return;
        
        // Start the animation
        animator.showAnimation(savedState.animation);
        
        // Calculate elapsed time since save
        const elapsed = Date.now() - savedState.timestamp;
        
        // Calculate which frame we should be on based on elapsed time
        let targetFrame = savedState.frameIndex;
        let timeInFrame = elapsed;
        
        // Sum up frame durations to find current frame
        let cumulativeTime = 0;
        for (let i = savedState.frameIndex; i < animation.frames.length; i++) {
            const frameDuration = animation.frames[i].duration || 100;
            cumulativeTime += frameDuration;
            
            if (timeInFrame <= cumulativeTime) {
                targetFrame = i;
                break;
            } else {
                timeInFrame -= frameDuration;
            }
        }
        
        // If we've exceeded the animation, just start from beginning
        if (targetFrame >= animation.frames.length) {
            targetFrame = 0;
            timeInFrame = 0;
        }
        
        // Set the frame index and frame data
        animator._currentFrameIndex = targetFrame;
        animator._currentFrame = animation.frames[targetFrame];
        
        // Draw the current frame immediately
        animator._draw();
        
        // Calculate remaining time in current frame
        const currentFrame = animation.frames[targetFrame];
        const frameDuration = currentFrame ? (currentFrame.duration || 100) : 100;
        const remainingTime = Math.max(0, frameDuration - timeInFrame);
        
        // Continue animation loop after remaining time
        setTimeout(function() {
            animator._step();
        }, remainingTime);
        
        // Restore balloon speech if it was active and we have the full text
        if (savedState.balloonActive && savedState.balloonText) {
            setTimeout(function() {
                agent.speak(savedState.balloonText, savedState.balloonHold || false);
                // Clear stored speech text after restoring
                sessionStorage.removeItem(SPEECH_TEXT_KEY);
            }, 100);
        }
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
        
        // Check for saved animation state (only restore for active sessions)
        const savedAnimationState = sessionStorage.getItem(ANIMATION_STATE_KEY);
        let parsedState = null;
        if (savedAnimationState && !isNewSession && !shouldAnimate) {
            try {
                parsedState = JSON.parse(savedAnimationState);
                // Only restore if saved recently (within 10 seconds)
                if (Date.now() - parsedState.timestamp > 10000) {
                    parsedState = null;
                    // Clear expired saved state
                    sessionStorage.removeItem(ANIMATION_STATE_KEY);
                }
            } catch (e) {
                console.warn('Failed to parse saved animation state:', e);
                // Clear invalid saved state
                sessionStorage.removeItem(ANIMATION_STATE_KEY);
            }
        } else if (savedAnimationState && (isNewSession || shouldAnimate)) {
            // Clear saved state if we're in a new session or using animations
            sessionStorage.removeItem(ANIMATION_STATE_KEY);
            sessionStorage.removeItem(SPEECH_TEXT_KEY);
        }
        
        // Determine if we're going to restore animation state
        const willRestoreAnimation = parsedState && !shouldAnimate;
        
        // Load the agent
        clippy.load(agentName, function(agent) {
            // Store agent globally so we can save state on unload
            window.currentClippyAgent = agent;
            
            // Hook into speak function to track full speech text
            hookClippySpeech(agent);
            
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
            
            // Track if saved state was cleared by page-specific animation
            let savedStateCleared = false;
            
            // Restore animation state if available (only for active sessions, not new ones)
            let restoreTimeout = null;
            if (willRestoreAnimation) {
                restoreTimeout = setTimeout(function() {
                    // Check if saved state still exists (might have been cleared by page-specific animation)
                    if (!savedStateCleared && sessionStorage.getItem(ANIMATION_STATE_KEY)) {
                        restoreClippyAnimation(agent, parsedState);
                        // Clear saved state after restoring
                        sessionStorage.removeItem(ANIMATION_STATE_KEY);
                    }
                }, 100);
            } else if (savedAnimationState) {
                // Clear any remaining saved state if we're not restoring
                sessionStorage.removeItem(ANIMATION_STATE_KEY);
                sessionStorage.removeItem(SPEECH_TEXT_KEY);
            }
            
            // On new session, speak the welcome message
            if (isNewSession) {
                setTimeout(function() {
                    agent.speak('If you need help, just ask! Or don\'t. I\'ll still be here. Watching.');
                }, shouldAnimate ? 500 : 100); // Small delay to let show animation complete if used
            }
            
            // Call custom onLoad callback
            // Pass willRestoreAnimation flag so page-specific animations can be skipped if restoring
            // Also pass a function to clear saved state if page-specific animation needs to play
            if (onLoad) {
                const clearSavedState = function() {
                    savedStateCleared = true;
                    sessionStorage.removeItem(ANIMATION_STATE_KEY);
                    sessionStorage.removeItem(SPEECH_TEXT_KEY);
                    // Cancel scheduled restore if it exists
                    if (restoreTimeout) {
                        clearTimeout(restoreTimeout);
                    }
                };
                onLoad(agent, sessionState, willRestoreAnimation, clearSavedState);
            }
            
            // Call onNewSession callback if applicable
            if (isNewSession && onNewSession) {
                onNewSession(agent);
            }
        });
    };
    
    // Save animation state before page unload
    window.addEventListener('beforeunload', function() {
        if (window.currentClippyAgent) {
            saveClippyAnimationState(window.currentClippyAgent);
        }
    });
    
    // Also save on pagehide (more reliable than beforeunload)
    window.addEventListener('pagehide', function() {
        if (window.currentClippyAgent) {
            saveClippyAnimationState(window.currentClippyAgent);
        }
    });
    
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

