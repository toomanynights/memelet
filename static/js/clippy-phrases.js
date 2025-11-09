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
     * Extract number from stat badge element
     * @param {string} className - Class name of the stat badge (e.g., 'total', 'error')
     * @returns {number|null} Extracted number or null if not found
     */
    function getStatBadgeNumber(className) {
        const badge = document.querySelector('.stat-badge.' + className);
        if (!badge) return null;
        
        // Extract number from text like "Total: 123" or "Errors: 5"
        const text = badge.textContent || badge.innerText || '';
        const match = text.match(/:\s*(\d+)/);
        if (match && match[1]) {
            return parseInt(match[1], 10);
        }
        return null;
    }
    
    /**
     * Extract number of results from results-info element
     * @returns {number|null} Number of search results or null if not found
     */
    function getSearchResultsCount() {
        const resultsInfo = document.querySelector('.results-info');
        if (!resultsInfo) return null;
        
        // Extract number from text like "Showing 5 result(s)"
        const text = resultsInfo.textContent || resultsInfo.innerText || '';
        const match = text.match(/Showing\s+(\d+)\s+result/i);
        if (match && match[1]) {
            return parseInt(match[1], 10);
        }
        return null;
    }
    
    /**
     * Process placeholder keys in phrases (e.g., {weekday}, {date})
     * @param {string} phrase - Phrase with potential placeholders
     * @returns {Object} {phrase: string, needsReroll: boolean}
     */
    function processPhrasePlaceholders(phrase) {
        if (!phrase || typeof phrase !== 'string') {
            return { phrase: phrase, needsReroll: false };
        }
        
        // Get placeholder definitions from the single source
        const defs = getPlaceholderDefinitions();
        const placeholders = defs.placeholders;
        
        // Replace all placeholders in the phrase
        let processedPhrase = phrase;
        for (let key in placeholders) {
            if (placeholders.hasOwnProperty(key)) {
                const regex = new RegExp('\\{' + key + '\\}', 'gi');
                processedPhrase = processedPhrase.replace(regex, placeholders[key]());
            }
        }
        
        return { phrase: processedPhrase, needsReroll: defs.needsReroll() };
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
        
        // Try to select a phrase that doesn't need rerolling
        const maxAttempts = 10; // Prevent infinite loops
        let excludedIndices = [];
        
        for (let attempt = 0; attempt < maxAttempts; attempt++) {
            // Get available indices (not excluded)
            const availableIndices = [];
            for (let i = 0; i < allPhrases.length; i++) {
                if (excludedIndices.indexOf(i) === -1) {
                    availableIndices.push(i);
                }
            }
            
            // If we've excluded everything, reset and use any phrase
            if (availableIndices.length === 0) {
                excludedIndices = [];
                break;
            }
            
            // Select random phrase from available ones
            const randomIndex = availableIndices[Math.floor(Math.random() * availableIndices.length)];
            const selectedPhrase = allPhrases[randomIndex];
            
            // Process placeholders in the phrase
            const result = processPhrasePlaceholders(selectedPhrase);
            
            // If it doesn't need rerolling, use it
            if (!result.needsReroll) {
                return result.phrase;
            }
            
            // Otherwise, exclude this phrase and try again
            excludedIndices.push(randomIndex);
        }
        
        // Fallback: just use a random phrase even if it needs rerolling
        const randomIndex = Math.floor(Math.random() * allPhrases.length);
        const selectedPhrase = allPhrases[randomIndex];
        const result = processPhrasePlaceholders(selectedPhrase);
        return result.phrase;
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
     * Get placeholder definitions
     * @returns {Object} Placeholder functions
     */
    function getPlaceholderDefinitions() {
        const now = new Date();
        let needsReroll = false;
        
        return {
            placeholders: {
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
                },
                'memes': function() {
                    const count = getStatBadgeNumber('total');
                    if (count === null || count === 0) {
                        needsReroll = true;
                        return '0';
                    }
                    return count.toString();
                },
                'errors': function() {
                    const count = getStatBadgeNumber('error');
                    if (count === null || count === 0) {
                        needsReroll = true;
                        return '0';
                    }
                    return count.toString();
                },
                'found': function() {
                    const count = getSearchResultsCount();
                    if (count === null || count === 0) {
                        needsReroll = true;
                        return '0';
                    }
                    return count.toString();
                }
            },
            needsReroll: function() { return needsReroll; },
            setNeedsReroll: function(value) { needsReroll = value; }
        };
    }
    
    /**
     * Process phrase placeholders (exposed for internal use)
     * @param {string} phrase - Phrase with placeholders
     * @returns {string} Processed phrase
     */
    window.processPhrasePlaceholders = function(phrase) {
        const result = processPhrasePlaceholders(phrase);
        return typeof result === 'object' ? result.phrase : result;
    };
    
    /**
     * Get random phrase (exposed for internal use)
     * @param {string|string[]} categories - Categories
     * @returns {string} Random phrase
     */
    window.getRandomClippyPhrase = getRandomPhrase;
    
    /**
     * Get list of available placeholder keys (exposed for debug tools)
     * Extracts keys directly from the placeholder definitions
     * @returns {string[]} Array of placeholder keys
     */
    window.getAvailablePlaceholders = function() {
        const defs = getPlaceholderDefinitions();
        return Object.keys(defs.placeholders);
    };
})();

