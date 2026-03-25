import requests, time

img="/home/gera/Downloads/Telegram Desktop/photo_2026-03-15_21-26-54 (2).jpg" # sbagliato
# img="/home/gera/Downloads/Telegram Desktop/photo_2026-03-15_21-26-54.jpg" # buono 
# img = "/home/gera/Downloads/Telegram Desktop/photo_2026-03-15_21-26-54 (3).jpg" # completa

prompt = """
Ci sono simboli, esclusi i triangoli blu, che vengono disegnati fuori dalla linea retta che si scorge in sottofondo?
Sotto c'è un xcwaterfall e sopra dei punti individuati dal software per il tracciamento di un pig. 
In particolare ti chiedo di controllare la parte finale del tracciamento, diciamo gli ultimi 10 punti, 
e verificare se i simboli cadono più o meno sulla linea tracciata dall'xcwaterfall oppure se alcuni simboli cadono chiaramente fuori da questa linea. 
Se ci sono simboli fuori linea ti chiedo di indicarlo e di dire a quale punto del tracciamento corrispondono (sempre riferendoti agli ultimi 10 punti). 
Nota che la linea tracciata dall'xcwaterfall potrebbe essere parzialmente nascosta dai triangolini blu. 
Rispondimi solo con un JSON che contiene tre valori: decisione (si/no), confidence (percentuale di sicurezza), commento (massimo due frasi)."
"""

prompt="ci sono simboli, esclusi i tringoli blu, che vengono disegnati fuori dalla rinea retta che si scorge in sottofondo ? in pratica sotto c'è un xcwaterfall sopra dei punti individuati dal sw per il tracciamento di un pig. In particolare ti chiedo se nella parte finale diciamo gli ultimi 10 punti i simboli cadono più o meno sulla linea tracciata dal xcwaterfall o se invece ci sono simboli che cadono fuori da questa linea. Se ci sono simboli che cadono fuori da questa linea, ti chiedo di indicarmeli e di dirmi a quale punto del tracciamento corrispondono. nota che la rinea retta tracciata dall'xc waterfall potrebbe essere nascosta anche dai trtiangolini blu. Rispondimi con un json che contine 3 valori decisione:si/no, una percentuale di sicurezza, e una piccola considerazione 2 frasi al massimo",
with open(img, "rb") as f:
    r = requests.post(
        "http://localhost:8000/ask-image",
        data={
            "prompt": prompt,
            "model": "gpt-4o"
        },
        files={"file": ("image.png", f, "image/png")}
    )

job_id = r.json()["job_id"]

# Poll
while True:
    res = requests.get(f"http://localhost:8000/result/{job_id}").json()
    if res["status"] == "JobStatus.FINISHED":
        print(res["result"]["content"])
        break
    time.sleep(1)