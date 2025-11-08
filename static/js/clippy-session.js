/**
 * Clippy Session Management
 * Tracks 3-hour browser sessions using localStorage
 * Manages animation state and speech across page navigation
 * 
 * Dependencies: clippy-phrases.js (for phrase loading/processing)
 */

(function() {
    'use strict';
    
    const SESSION_KEY = 'clippy_session_time';
    const SESSION_DURATION = 3 * 60 * 60 * 1000; // 3 hours in milliseconds
    const ANIMATION_STATE_KEY = 'clippy_animation_state';
    const SPEECH_TEXT_KEY = 'clippy_speech_text';
    const PAGE_SPECIFIC_ANIMS = ['Searching', 'CheckingSomething']; // Animations to drop on navigation
    
    // Random quip tracking
    const QUIP_LAST_TIME_KEY = 'clippy_last_quip_time';
    const QUIP_PAGE_COUNT_KEY = 'clippy_pages_since_quip';
    const QUIP_MIN_TIME = 5 * 60 * 1000; // 5 minutes in milliseconds
    const QUIP_MIN_PAGES = 20;
    const QUIP_RANDOM_CHANCE = 0.25; // 25% chance (>75% threshold)
    
    /**
     * Detect which page we're currently on
     * @returns {string} Page identifier ('meme_detail', 'index', 'search', 'settings', 'tags')
     */
    function detectCurrentPage() {
        const path = window.location.pathname;
        const search = window.location.search;
        
        if (path.startsWith('/meme/')) {
            return 'meme_detail';
        } else if (path === '/settings') {
            return 'settings';
        } else if (path === '/tags') {
            return 'tags';
        } else if (path === '/' && search && search.indexOf('search=') !== -1) {
            return 'search';
        } else {
            return 'index';
        }
    }
    
    /**
     * Get appropriate quip categories for current page
     * @returns {string|string[]} Category or array of categories
     */
    function getQuipCategoriesForPage() {
        const page = detectCurrentPage();
        
        if (page === 'meme_detail') {
            // On meme detail page, draw from both random and meme_page
            return ['random', 'meme_page'];
        } else if (page === 'index') {
            // On index page, draw from both index and random
            return ['index', 'random'];
        } else if (page === 'search') {
            // On search page, ONLY use search category
            return 'search';
        } else {
            // On other pages, just use random
            return 'random';
        }
    }
    
    /**
     * Record that a quip was spoken (reset page counter and update timestamp)
     */
    function recordQuip() {
        localStorage.setItem(QUIP_LAST_TIME_KEY, Date.now().toString());
        localStorage.setItem(QUIP_PAGE_COUNT_KEY, '0');
    }
    
    /**
     * Increment page counter (called on each page load)
     */
    function incrementPageCount() {
        const currentCount = parseInt(localStorage.getItem(QUIP_PAGE_COUNT_KEY) || '0', 10);
        localStorage.setItem(QUIP_PAGE_COUNT_KEY, (currentCount + 1).toString());
    }
    
    /**
     * Check if random quip should be triggered
     * @returns {boolean} True if quip should fire
     */
    function shouldTriggerRandomQuip() {
        const lastQuipTime = parseInt(localStorage.getItem(QUIP_LAST_TIME_KEY) || '0', 10);
        const pagesSinceQuip = parseInt(localStorage.getItem(QUIP_PAGE_COUNT_KEY) || '0', 10);
        const now = Date.now();
        const timeSinceQuip = now - lastQuipTime;
        
        // Must be more than 5 minutes since last quip
        if (timeSinceQuip <= QUIP_MIN_TIME) {
            return false;
        }
        
        // Either random chance >25% OR pages since last quip > 20
        const randomRoll = Math.random();
        const passedRandomCheck = randomRoll > (1 - QUIP_RANDOM_CHANCE);
        const passedPageCheck = pagesSinceQuip > QUIP_MIN_PAGES;
        
        return passedRandomCheck || passedPageCheck;
    }
    
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
            
            // Increment page count on every page load
            incrementPageCount();
            
            // Check if we'll be speaking a quip
            const willSpeakQuip = isNewSession || shouldTriggerRandomQuip();
            
            // Detect if we're on a page with page-specific animations (where quip will be handled by page-specific logic)
            const currentPage = detectCurrentPage();
            const isSearchPage = currentPage === 'search';
            const isSettingsPage = currentPage === 'settings';
            const hasPageSpecificAnimation = isSearchPage || isSettingsPage;
            
            // On new session, speak the welcome message (unless on page with page-specific animation)
            if (isNewSession && !hasPageSpecificAnimation) {
                setTimeout(function() {
                    // Use phrase loading from clippy-phrases.js
                    window.loadClippyPhrases().then(function() {
                        const welcomePhrase = window.getRandomClippyPhrase(['welcome', 'random']);
                        agent.speak(welcomePhrase);
                        // Welcome speech counts as a quip
                        recordQuip();
                    });
                }, shouldAnimate ? 500 : 100); // Small delay to let show animation complete if used
            } else if (!isNewSession && !hasPageSpecificAnimation) {
                // Not a new session - check if we should trigger a random quip (skip if page has page-specific animation)
                const shouldQuip = shouldTriggerRandomQuip();
                if (shouldQuip) {
                    setTimeout(function() {
                        window.loadClippyPhrases().then(function() {
                            const categories = getQuipCategoriesForPage();
                            const randomQuip = window.getRandomClippyPhrase(categories);
                            agent.speak(randomQuip);
                            recordQuip();
                        });
                    }, shouldAnimate ? 500 : 100);
                }
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
                onLoad(agent, sessionState, willRestoreAnimation, clearSavedState, willSpeakQuip);
            }
            
            // Call onNewSession callback if applicable
            if (isNewSession && onNewSession) {
                onNewSession(agent);
            }
        });
    };
    
    function clearClippyStoredState() {
        sessionStorage.removeItem(ANIMATION_STATE_KEY);
        sessionStorage.removeItem(SPEECH_TEXT_KEY);
    }

    function clearSessionTimer() {
        localStorage.removeItem(SESSION_KEY);
    }

    /**
     * Hide and fully teardown the current agent instance
     * @param {Object} options
     * @param {boolean} options.fastHide - Skip hide animation
     * @param {boolean} options.skipIfMissing - Resolve immediately when no agent is active
     * @returns {Promise<void>}
     */
    window.destroyClippyAgent = function(options) {
        options = options || {};
        const fastHide = options.fastHide || false;
        const skipIfMissing = options.skipIfMissing !== undefined ? options.skipIfMissing : true;

        const activeAgent = window.currentClippyAgent;

        if (!activeAgent) {
            if (!skipIfMissing) {
                clearClippyStoredState();
                clearSessionTimer();
            }
            return Promise.resolve();
        }

        return new Promise(function(resolve) {
            try {
                const cleanup = function() {
                    try {
                        if (activeAgent._balloon && activeAgent._balloon._balloon) {
                            activeAgent._balloon._balloon.remove();
                        }
                        if (activeAgent._el && activeAgent._el.remove) {
                            activeAgent._el.remove();
                        }
                    } catch (removeError) {
                        console.warn('Error removing Clippy elements:', removeError);
                    }

                    window.currentClippyAgent = null;
                    clearClippyStoredState();
                    if (!options.preserveSession) {
                        clearSessionTimer();
                    }
                    resolve();
                };

                activeAgent.hide(!!fastHide, cleanup);
            } catch (e) {
                console.warn('Error tearing down Clippy agent:', e);
                window.currentClippyAgent = null;
                clearClippyStoredState();
                if (!options.preserveSession) {
                    clearSessionTimer();
                }
                resolve();
            }
        });
    };

    /**
     * Unified agent loader that optionally tears down the existing agent first.
     * @param {string|null} agentName - Target agent name or null/'none' to disable.
     * @param {Object} options
     * @param {boolean} options.useAnimations - Force show animation even within active sessions.
     * @param {boolean} options.fastHide - Skip hide animation when tearing down.
     * @param {boolean} options.skipTeardown - Do not teardown before loading (useful on first load).
     * @param {Function} options.onLoad - Callback invoked after agent loads (mirrors initClippyWithSession).
     * @param {Function} options.onNewSession - Callback passed through to initClippyWithSession.
     * @returns {Promise<Object|null>} Resolves with loaded agent or null if disabled.
     */
    window.setClippyAgent = function(agentName, options) {
        options = options || {};
        const normalizedName = (!agentName || agentName === 'none') ? null : agentName;
        const useAnimations = options.useAnimations || false;
        const fastHide = options.fastHide || false;
        const skipTeardown = options.skipTeardown || false;
        const onLoad = options.onLoad;
        const onNewSession = options.onNewSession;

        const prepare = skipTeardown ? Promise.resolve() : window.destroyClippyAgent({ fastHide: fastHide, preserveSession: !!options.preserveSession });

        return prepare.then(function() {
            if (!normalizedName) {
                return null;
            }

            return new Promise(function(resolve, reject) {
                try {
                    initClippyWithSession(normalizedName, {
                        useAnimations: useAnimations,
                        onLoad: function(agent, sessionState, willRestoreAnimation, clearSavedState, willSpeakQuip) {
                            try {
                                window.currentClippyAgent = agent;
                                if (typeof onLoad === 'function') {
                                    onLoad(agent, sessionState, willRestoreAnimation, clearSavedState, willSpeakQuip);
                                }
                            } catch (callbackError) {
                                console.warn('Error in Clippy onLoad callback:', callbackError);
                            }
                            resolve(agent);
                        },
                        onNewSession: onNewSession
                    });
                } catch (error) {
                    reject(error);
                }
            });
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
})();
