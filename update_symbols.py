import json
import logging
import os

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def update_json_file(new_data):
    filename = "all_symbols.json"
    try:
        if os.path.exists(filename):
            with open(filename, "r", encoding="utf-8") as f:
                current_data = json.load(f)
        else:
            current_data = {}
        
        current_data.update(new_data)
        
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(current_data, f, ensure_ascii=False, indent=4)
        logger.info(f"{filename} başarıyla güncellendi.")
    except Exception as e:
        logger.error(f"Dosya güncelleme hatası: {e}")

if __name__ == "__main__":
    # Bu script manuel olarak veya bir scraper tarafından çağrılabilir
    pass
