/**
 * Clippy Phrase Management
 * Handles loading, processing, and selecting phrases
 */

(function() {
    'use strict';
    
    // Cache for loaded phrases
    let clippyPhrases = null;
    
    /**
     * Load Clippy phrases from JSON file
     * @returns {Promise<Object>} Phrases object
     */
    function loadClippyPhrases() {
        if (clippyPhrases) {
            return Promise.resolve(clippyPhrases);
        }
        
        return fetch('/static/js/clippy-phrases.json')
            .then(function(response) {
                if (!response.ok) {
                    throw new Error('Failed to load phrases');
                }
                return response.json();
            })
            .then(function(data) {
                clippyPhrases = data;
                return data;
            })
            .catch(function(error) {
                console.warn('Failed to load Clippy phrases:', error);
                // Return fallback phrase
                return {
                    welcome: ["If you need help, just ask! Or don't. I'll still be here. Watching."]
                };
            });
    }
    
    /**
     * Process placeholder keys in phrases (e.g., {weekday}, {date})
     * @param {string} phrase - Phrase with potential placeholders
     * @returns {string} Phrase with placeholders replaced
     */
    function processPhrasePlaceholders(phrase) {
        if (!phrase || typeof phrase !== 'string') {
            return phrase;
        }
        
        const now = new Date();
        
        // Define placeholder replacements
        const placeholders = {
            'weekday': function() {
                const days = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
                return days[now.getDay()];
            },
            'date': function() {
                return now.toLocaleDateString();
            },
            'time': function() {
                return now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
            },
            'month': function() {
                const months = ['January', 'February', 'March', 'April', 'May', 'June', 
                               'July', 'August', 'September', 'October', 'November', 'December'];
                return months[now.getMonth()];
            },
            'year': function() {
                return now.getFullYear().toString();
            },
            'day': function() {
                return now.getDate().toString();
            }
        };
        
        // Replace all placeholders in the phrase
        let processedPhrase = phrase;
        for (let key in placeholders) {
            if (placeholders.hasOwnProperty(key)) {
                const regex = new RegExp('\\{' + key + '\\}', 'gi');
                processedPhrase = processedPhrase.replace(regex, placeholders[key]());
            }
        }
        
        return processedPhrase;
    }
    
    /**
     * Get a random phrase from one or more categories
     * @param {string|string[]} categories - Category name or array of category names (e.g., 'welcome', ['welcome', 'random'])
     * @returns {string} Random phrase or fallback
     */
    function getRandomPhrase(categories) {
        if (!clippyPhrases) {
            return "If you need help, just ask! Or don't. I'll still be here. Watching.";
        }
        
        // Normalize to array
        const categoryArray = Array.isArray(categories) ? categories : [categories];
        
        // Collect all phrases from specified categories
        let allPhrases = [];
        for (let i = 0; i < categoryArray.length; i++) {
            const category = categoryArray[i];
            if (clippyPhrases[category] && clippyPhrases[category].length > 0) {
                allPhrases = allPhrases.concat(clippyPhrases[category]);
            }
        }
        
        // Fallback if no phrases found
        if (allPhrases.length === 0) {
            return "If you need help, just ask! Or don't. I'll still be here. Watching.";
        }
        
        // Select random phrase
        const randomIndex = Math.floor(Math.random() * allPhrases.length);
        const selectedPhrase = allPhrases[randomIndex];
        
        // Process placeholders in the phrase
        return processPhrasePlaceholders(selectedPhrase);
    }
    
    // Expose functions globally
    
    /**
     * Get a random Clippy phrase from one or more categories
     * Usage: getClippyPhrase('welcome') or getClippyPhrase(['welcome', 'random'])
     * @param {string|string[]} categories - Category name or array of category names
     * @returns {Promise<string>} Promise that resolves to a random phrase
     */
    window.getClippyPhrase = function(categories) {
        return loadClippyPhrases().then(function() {
            return getRandomPhrase(categories);
        });
    };
    
    /**
     * Load phrases (exposed for internal use by other Clippy modules)
     * @returns {Promise<Object>} Phrases object
     */
    window.loadClippyPhrases = loadClippyPhrases;
    
    /**
     * Process phrase placeholders (exposed for internal use)
     * @param {string} phrase - Phrase with placeholders
     * @returns {string} Processed phrase
     */
    window.processPhrasePlaceholders = processPhrasePlaceholders;
    
    /**
     * Get random phrase (exposed for internal use)
     * @param {string|string[]} categories - Categories
     * @returns {string} Random phrase
     */
    window.getRandomClippyPhrase = getRandomPhrase;
})();

