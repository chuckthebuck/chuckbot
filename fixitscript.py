import pywikibot
from pywikibot import pagegenerators

# Set the site
site = pywikibot.Site('en', 'commons')  # Adjust language and project as needed

# Define the parent category you want to work with
parent_category_name = 'Category:Offending GLAM master cat'  # Replace with your actual category name
parent_category = pywikibot.Category(site, parent_category_name)

# Create a generator for all pages in the parent category and its subcategories
pages = pagegenerators.CategorizedPageGenerator(parent_category)

# Iterate through each page in the parent category and its subcategories
for page in pages:
    # Check if the page is a file
    if page.namespace() == 6:  # Namespace 6 is for files
        new_categories = []
        # Get current categories
        for cat in page.categories():
            # Create new category name by removing the word "the"
            new_cat_name = cat.title().replace(' the ', ' ').strip()  # Remove "the" and clean up spaces
            if new_cat_name:
                new_categories.append(new_cat_name)
        
        # Move the page to new categories if there are changes
        if new_categories:
            # Remove duplicates and ensure valid categories
            new_categories = list(set(new_categories))
            page.put(new_categories, summary='Removing the word "the" from categories')

print("Category updates completed.")
