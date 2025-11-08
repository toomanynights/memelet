/**
 * Clippy Debug Functions
 * Utilities for testing and debugging Clippy behavior
 */

(function() {
    'use strict';
    
    const SESSION_KEY = 'clippy_session_time';
    const SESSION_DURATION = 3 * 60 * 60 * 1000; // 3 hours in milliseconds
    
    // Random quip tracking keys (must match clippy-session.js)
    const QUIP_LAST_TIME_KEY = 'clippy_last_quip_time';
    const QUIP_PAGE_COUNT_KEY = 'clippy_pages_since_quip';
    const QUIP_MIN_TIME = 5 * 60 * 1000; // 5 minutes in milliseconds
    const QUIP_MIN_PAGES = 20;
    
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

    /**
 * Debug: List all available animations for current agent
 * Usage: listClippyAnimations()
 */
window.listClippyAnimations = function() {
    if (!window.currentClippyAgent) {
        console.error('Clippy agent not loaded yet. Please wait for the agent to initialize.');
        return;
    }
    
    const agent = window.currentClippyAgent;
    const animations = agent._animator._data.animations;
    
    if (!animations) {
        console.error('No animation data available.');
        return;
    }
    
    console.log('Available animations for current agent:');
    console.log('─'.repeat(60));
    
    const animList = Object.keys(animations).sort().map(name => {
        const anim = animations[name];
        const frameCount = anim.frames ? anim.frames.length : 0;
        const duration = anim.frames ? anim.frames.reduce((sum, f) => sum + (f.duration || 0), 0) : 0;
        return {
            name: name,
            frames: frameCount,
            duration: duration + 'ms'
        };
    });
    
    console.table(animList);
    console.log('─'.repeat(60));
    console.log('To play an animation, use: playClippyAnimation("AnimationName")');
    console.log('Example: playClippyAnimation("Show")');
    
    return animList;
};

/**
 * Debug: Play a specific animation
 * Usage: playClippyAnimation("Show") or playClippyAnimation("Wave", 3000)
 * @param {string} animationName - Name of the animation to play
 * @param {number} [duration] - Optional duration override in milliseconds
 */
window.playClippyAnimation = function(animationName, duration) {
    if (!window.currentClippyAgent) {
        console.error('Clippy agent not loaded yet. Please wait for the agent to initialize.');
        return;
    }
    
    if (!animationName || typeof animationName !== 'string') {
        console.error('Please provide a valid animation name.');
        console.log('Use listClippyAnimations() to see all available animations.');
        return;
    }
    
    const agent = window.currentClippyAgent;
    const animations = agent._animator._data.animations;
    
    if (!animations[animationName]) {
        console.error('Animation "' + animationName + '" not found.');
        console.log('Available animations:', Object.keys(animations).sort().join(', '));
        return;
    }
    
    console.log('Playing animation:', animationName);
    
    if (duration) {
        agent.play(animationName, duration);
    } else {
        agent.play(animationName);
    }
};

/**
 * Debug: Show detailed info about a specific animation
 * Usage: inspectClippyAnimation("Show")
 * @param {string} animationName - Name of the animation to inspect
 */
window.inspectClippyAnimation = function(animationName) {
    if (!window.currentClippyAgent) {
        console.error('Clippy agent not loaded yet. Please wait for the agent to initialize.');
        return;
    }
    
    if (!animationName || typeof animationName !== 'string') {
        console.error('Please provide a valid animation name.');
        return;
    }
    
    const agent = window.currentClippyAgent;
    const animations = agent._animator._data.animations;
    
    if (!animations[animationName]) {
        console.error('Animation "' + animationName + '" not found.');
        return;
    }
    
    const anim = animations[animationName];
    console.log('Animation:', animationName);
    console.log('─'.repeat(60));
    console.log('Frames:', anim.frames.length);
    console.log('Total duration:', anim.frames.reduce((sum, f) => sum + (f.duration || 0), 0) + 'ms');
    console.log('Has branching:', anim.frames.some(f => f.branching) ? 'Yes' : 'No');
    console.log('Has sounds:', anim.frames.some(f => f.sound) ? 'Yes' : 'No');
    console.log('Use exit branches:', anim.useExitBranching || false);
    console.log('─'.repeat(60));
    
    console.log('Frame details:');
    anim.frames.forEach((frame, idx) => {
        const details = [];
        details.push('Frame ' + idx);
        details.push('duration: ' + frame.duration + 'ms');
        if (frame.images) details.push('images: ' + frame.images.length);
        if (frame.sound) details.push('sound: ' + frame.sound);
        if (frame.exitBranch !== undefined) details.push('exitBranch: ' + frame.exitBranch);
        if (frame.branching) details.push('has branching');
        console.log('  ' + details.join(', '));
    });
    
    return anim;
};

/**
 * Debug: Stop current animation
 * Usage: stopClippyAnimation()
 */
window.stopClippyAnimation = function() {
    if (!window.currentClippyAgent) {
        console.error('Clippy agent not loaded yet. Please wait for the agent to initialize.');
        return;
    }
    
    const agent = window.currentClippyAgent;
    agent.stop();
    console.log('Animation stopped.');
};

/**
 * Debug: Compare animations across agents (useful for finding misnamed animations)
 * Usage: compareAgentAnimations()
 */
window.compareAgentAnimations = function() {
    console.log('Available agents to compare:');
    console.log('Bonzi, Clippy, F1, Genie, Genius, Links, Merlin, Peedy, Rocky, Rover');
    console.log('─'.repeat(60));
    console.log('Note: This only shows what the current agent has loaded.');
    console.log('To compare, you need to load different agents and check manually.');
    
    if (window.currentClippyAgent) {
        console.log('Current agent has these animations:');
        const anims = Object.keys(window.currentClippyAgent._animator._data.animations).sort();
        console.log(anims.join(', '));
    }
};

/**
 * Debug: Check random quip status
 * Usage: debugClippyQuips()
 */
window.debugClippyQuips = function() {
    const lastQuipTime = parseInt(localStorage.getItem(QUIP_LAST_TIME_KEY) || '0', 10);
    const pagesSinceQuip = parseInt(localStorage.getItem(QUIP_PAGE_COUNT_KEY) || '0', 10);
    const now = Date.now();
    const timeSinceQuip = now - lastQuipTime;
    const minutesSinceQuip = (timeSinceQuip / (60 * 1000)).toFixed(2);
    
    // Detect current page
    const path = window.location.pathname;
    const search = window.location.search;
    let currentPage = 'index';
    let quipCategories = ['index', 'random'];
    
    if (path.startsWith('/meme/')) {
        currentPage = 'meme_detail';
        quipCategories = ['random', 'meme_page'];
    } else if (path === '/settings') {
        currentPage = 'settings';
        quipCategories = ['settings', 'random'];
    } else if (path === '/tags') {
        currentPage = 'tags';
        quipCategories = ['random'];
    } else if (path === '/' && search && search.indexOf('search=') !== -1) {
        currentPage = 'search';
        quipCategories = ['search'];
    }
    
    console.log('═'.repeat(60));
    console.log('RANDOM QUIP STATUS');
    console.log('═'.repeat(60));
    console.log('Current page:', currentPage);
    console.log('Quip categories:', quipCategories.join(', '));
    console.log('─'.repeat(60));
    
    if (!lastQuipTime) {
        console.log('Last quip: NEVER (no quips recorded yet)');
    } else {
        console.log('Last quip:', new Date(lastQuipTime).toLocaleString());
        console.log('Time since:', minutesSinceQuip, 'minutes');
    }
    
    console.log('Pages since last quip:', pagesSinceQuip);
    console.log('─'.repeat(60));
    console.log('TRIGGER CONDITIONS:');
    console.log('  ✓ Time requirement: >' + (QUIP_MIN_TIME / 60000) + ' minutes');
    console.log('  ✓ Then EITHER:');
    console.log('    - Random chance >75% (25% probability)');
    console.log('    - OR pages since quip >' + QUIP_MIN_PAGES);
    console.log('─'.repeat(60));
    
    const meetsTimeReq = timeSinceQuip > QUIP_MIN_TIME;
    const meetsPageReq = pagesSinceQuip > QUIP_MIN_PAGES;
    
    console.log('CURRENT STATE:');
    console.log('  Time requirement met:', meetsTimeReq ? '✓ YES' : '✗ NO (need ' + ((QUIP_MIN_TIME - timeSinceQuip) / 60000).toFixed(2) + ' more minutes)');
    console.log('  Page requirement met:', meetsPageReq ? '✓ YES' : '✗ NO (need ' + (QUIP_MIN_PAGES - pagesSinceQuip + 1) + ' more pages)');
    
    if (meetsTimeReq) {
        console.log('  Status: READY (will check random roll on next page load)');
        if (meetsPageReq) {
            console.log('  Note: Page requirement also met, so quip WILL fire on next page load');
        } else {
            console.log('  Note: 25% chance to fire on next page load (random roll)');
        }
    } else {
        console.log('  Status: NOT READY (time requirement not met)');
    }
    
    console.log('═'.repeat(60));
    
    return {
        currentPage: currentPage,
        quipCategories: quipCategories,
        lastQuipTime: lastQuipTime,
        pagesSinceQuip: pagesSinceQuip,
        minutesSinceQuip: parseFloat(minutesSinceQuip),
        meetsTimeReq: meetsTimeReq,
        meetsPageReq: meetsPageReq
    };
};

/**
 * Debug: Force a quip to trigger on next page load
 * Usage: forceClippyQuip()
 */
window.forceClippyQuip = function() {
    // Set time since last quip to 6 minutes ago (just over the 5 minute threshold)
    const sixMinutesAgo = Date.now() - (6 * 60 * 1000);
    localStorage.setItem(QUIP_LAST_TIME_KEY, sixMinutesAgo.toString());
    
    // Set pages since last quip to 21 (just over the 20 page threshold)
    localStorage.setItem(QUIP_PAGE_COUNT_KEY, '21');
    
    console.log('═'.repeat(60));
    console.log('FORCE QUIP ENABLED');
    console.log('═'.repeat(60));
    console.log('✓ Time since last quip set to 6 minutes ago');
    console.log('✓ Pages since last quip set to 21');
    console.log('');
    console.log('A random quip WILL fire on the next page load.');
    console.log('Refreshing page in 2 seconds...');
    console.log('═'.repeat(60));
    
    setTimeout(function() {
        location.reload();
    }, 2000);
};

/**
 * Debug: Reset quip tracking (clear all quip data)
 * Usage: resetClippyQuips()
 */
window.resetClippyQuips = function() {
    localStorage.removeItem(QUIP_LAST_TIME_KEY);
    localStorage.removeItem(QUIP_PAGE_COUNT_KEY);
    
    console.log('═'.repeat(60));
    console.log('Quip tracking reset!');
    console.log('All quip data cleared from localStorage.');
    console.log('Refreshing page in 1 second...');
    console.log('═'.repeat(60));
    
    setTimeout(function() {
        location.reload();
    }, 1000);
};
})();

