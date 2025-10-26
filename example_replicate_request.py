"""
Example of what a Replicate request looks like for image analysis
based on the current code in process_memes.py
"""

# Example for an image file (photo_2025-10-26_15-30-21.jpg)

# The model being called
model = "openai/gpt-4o-mini"  # GPT-4o-mini with vision capabilities

# The input data structure
input_data = {
    "prompt": "This image is a meme. Analyze it and return json of the following structure: {references: \"Analyze the image to see if it features any famous persons or characters from movies, shows, cartoons or games. If it does, put that information here. If not, omit\", template: \"If the images features an established meme character or template (such as 'trollface', 'wojak', 'Pepe the Frog', 'Loss'), name it here, otherwise omit\", caption: \"If the image includes any captions, put them here in the original language, otherwise omit\", description: \"Describe the image with its captions (if any) in mind\", meaning: \"Explain what this meme means, using information you determined earlier\"}",
    "image_input": ["https://memes.tmn.name/files/photo_2025-10-26_15-30-21.jpg"],
    "system_prompt": "You're a meme expert. You're very smart and see meanings between the lines. You know all famous persons and all characters from every show, movie and game. Use correct meme names (like Pepe, Wojak, etc.) and media references.",
    "temperature": 1,
    "top_p": 1,
    "max_completion_tokens": 2048
}

# How it would be called:
import replicate

output = replicate.run(model, input=input_data)

# Then iterate over the output
for item in output:
    print(item)

