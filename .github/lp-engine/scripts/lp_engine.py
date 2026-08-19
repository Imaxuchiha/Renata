"""Portuguese With Renata - autonome landingspagina-motor (2 silos, full-auto MET kwaliteitspoort).

Zelfde bewezen ontwerp als de AC-blogmotor: DeepSeek schrijft ALLEEN proza als JSON, de
deterministische template hieronder rendert de HTML. Daardoor staan schema, prijzen, formulier,
interne links en merkfeiten VAST - het model kan de structuur niet breken en geen valse claim
(nep-review, beedigde vertaling, verzonnen prijs) live zetten.

Twee silos, want dit domein bedient twee losstaande zoekmarkten:
  learn/  -> Engelstalig, Braziliaans Portugees leren  (gemeten: ~12k zoekopdrachten/mnd)
  china/  -> pt-BR, importeren uit China + communicatie met Chinese leveranciers (~7k/mnd)

Pipeline per pagina:
  GSC striking-distance (pos 4-20) + gecureerde seed-backlog per silo
    -> topic-keuze (dedup vs manifest, skip wat een bestaande pagina al dekt)
    -> DeepSeek -> JSON -> kwaliteits- + dedup-poort (Jaccard < 0.42)
    -> render HTML -> schrijf -> hub herbouwen -> sitemap +1
  Publiceren = git commit + push naar main; Netlify bouwt zelf vanaf main.

Subcommands:
  py lp_engine.py topics                    # print de topic-queue van beide silos
  py lp_engine.py run --dry-run             # 1 pagina lokaal schrijven, niets vastleggen
  py lp_engine.py run                       # 1 pagina publiceren (silo op toerbeurt)
  py lp_engine.py run --count 2             # 2 paginas: 1 per silo
  py lp_engine.py run --silo china          # forceer silo
  py lp_engine.py run --silo china --topic "..."   # forceer 1 onderwerp
  py lp_engine.py hubs                      # herbouw alleen /learn/ en /china/

Env: DEEPSEEK_API_KEY, GSC_REFRESH_TOKEN, GSC_REFRESH_TOKEN_WRITE, GADS_CLIENT_ID, GADS_CLIENT_SECRET.
Alleen stdlib + de meegeleverde fleet-modules in deze map.
"""
import argparse
import datetime as _dt
import html as _html
import json
import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

from gsc_report import gsc_token, resolve_site, query, parse_rows
from deepseek_client import chat

# ---------------------------------------------------------------- config
SCRIPTS_DIR = Path(__file__).resolve().parent
DOMAIN = "portuguesewithrenata.com"
BASE_URL = "https://" + DOMAIN
SITE_DIR = Path(os.environ.get("RENATA_DIR", SCRIPTS_DIR.parents[2]))
GA4 = "G-74WWL8S9DB"
WA = "351935328206"

# Feiten die overal deterministisch vaststaan. Het model mag hier NIETS aan toevoegen.
RENATA_FACTS = """RENATA FACTS (use ONLY these for any price, claim, credential or contact):
- Renata is Brazilian. Brazilian Portuguese is her native language. She lives in Oeiras, near Lisbon.
- She studied in China at Hubei University and holds HSK 4. That is solid everyday and operational
  business Chinese. It is NOT legal, medical, technical or sworn-translation level. Say so plainly
  whenever her Chinese comes up. Never call her fluent, native or certified in Chinese.
- Teaching: Brazilian Portuguese, online, to English speakers. Evening hours only, a few per week.
  Capacity is genuinely small and that is stated honestly, never as a scarcity trick.
- She does NOT teach European Portuguese and does NOT prepare anyone for the CIPLE exam. If the topic
  comes up, say why: that exam tests European Portuguese and marks Brazilian usage wrong.
- Teaching prices: paid diagnostic lesson 45 EUR (credited against a later package, never a free trial);
  one-to-one evening lesson 65 EUR/hour; 5-lesson pack 300 EUR; small group of max 4 people 89 EUR/month;
  children 69 EUR/month. There is no A1-A2 self-study course on sale yet, only a waiting list.
- China service: live interpreting and message support between a Portuguese- or English-speaking buyer
  and a Chinese supplier. Video and WeChat calls, reading and writing supplier messages, explaining what
  an answer really means. On location in the Lisbon area 45 EUR/hour, minimum 2 hours. Remote 25 EUR per
  half hour.
- She is NOT a sourcing agent. She does not audit factories, inspect quality, handle samples, arrange
  freight, clear customs or guarantee a supplier. Never imply she does.
- She does NOT do sworn, certified, legal or medical document translation.
- Only contact channel: WhatsApp +351 935 328 206. No other phone number, no other email.
- There are no testimonials, reviews, ratings, student numbers, awards or case studies. Never invent one."""

# Twee klassen verboden taal.
#
# HARD = nooit toegestaan, in geen enkele vorm. Er is geen eerlijke zin waarin dit hoort.
FORBIDDEN_HARD = [
    "testimonial", "5-star", "5 star", "our students say", "trustpilot",
    "depoimento", "estrelas no google", "avaliacoes de clientes",
    "@gmail.com", "@outlook.com",
]

# CREDENTIAL = uitspraken over iemands niveau of bevoegdheid. Die gaan per definitie over een
# persoon, dus ze mogen alleen staan als er vlak ervoor ontkend wordt ("ze is GEEN beedigd
# vertaler"). Dat expliciete "wat ik niet doe" is juist de eerlijke lijn van dit merk.
FORBIDDEN_CREDENTIAL = [
    "fluent in chinese", "fluent in mandarin", "native chinese", "certified translator",
    "sworn translator", "tradutor juramentado", "tradutora juramentada",
    "nativa em chines", "fluente em chines", "fluente em mandarim",
]

# SERVICE = diensten die Renata niet levert. Dit zijn TEGELIJK gewone vaktermen die in een
# importgids gewoon uitgelegd moeten kunnen worden ("voor documenten heb je een despachante
# aduaneiro nodig"). Ze worden daarom alleen geweigerd als ze aan Renata of aan een wij-vorm
# gekoppeld zijn zonder ontkenning. Een neutrale uitleg mag.
FORBIDDEN_SERVICE = [
    "sourcing agent", "quality inspection", "factory audit", "freight forwarding",
    "despachante aduaneiro", "agente de compras", "inspecao de qualidade", "auditoria de fabrica",
    "sworn translation", "traducao juramentada", "traducao certificada",
    "ciple", "european portuguese lessons", "european portuguese course",
]

NEGATIONS = ("nao ", "nunca ", "sem ", "nem ", "not ", "no ", "never ", "without ", "neither ",
             "does not", "doesn t", "isn t", "is not", "cannot", "can t", "instead of",
             "em vez de", "diferente de")
# Woorden die aangeven dat de zin over Renata of over "ons aanbod" gaat.
SELF_MARKERS = ("renata", " ela ", " she ", " her ", " we ", " our ", " i ", " eu ", " nos ",
                "nosso", "nossa", "oferecemos", "oferece", "offers", "provides", "acompanha")
NEG_WINDOW = 80    # tekens terugkijken voor een ontkenning
SELF_WINDOW = 160  # tekens rondom kijken voor een verwijzing naar Renata of wij


def _negated_before(low, idx):
    """Staat er binnen NEG_WINDOW tekens voor positie idx een ontkenning?"""
    return any(n in low[max(0, idx - NEG_WINDOW):idx] for n in NEGATIONS)


def _about_renata(low, idx, span):
    """Gaat deze zin over Renata of over 'wij', of is het neutrale vakuitleg?"""
    window = low[max(0, idx - SELF_WINDOW):idx + span + SELF_WINDOW // 2]
    return any(m in window for m in SELF_MARKERS)

# ---------------------------------------------------------------- silos
SILOS = {
    "learn": {
        "dir": "learn",
        "lang": "en",
        "og_locale": "en_GB",
        "eyebrow_default": "Brazilian Portuguese",
        "hub_title": "Learn Brazilian Portuguese: free guides",
        "hub_desc": "Free, accurate guides to Brazilian Portuguese: pronunciation, everyday phrases, and where a generic Portuguese answer would send you wrong.",
        "hub_h1": "Brazilian Portuguese, explained properly",
        "hub_intro": "Short guides written by a Brazilian teacher, not copied from a phrasebook. Every page says what Brazilians actually say, and flags the places where an answer written for European Portuguese would send you wrong.",
        "hub_cta_label": "See lessons and prices",
        "hub_cta_href": "/pricing.html",
        "hub_back": "Free resources",
        "covered": ["brazilian portuguese vs european", "brazilian vs european portuguese",
                    "difference between brazilian and european", "pricing", "cost of lessons"],
        "wa_text": "Hi%20Renata%2C%20I%27d%20like%20to%20learn%20Brazilian%20Portuguese.",
        "cta_title": "Learning this with someone is faster",
        "cta_body": "Renata teaches Brazilian Portuguese one to one, in the evening, in small numbers. A paid diagnostic lesson is 45 EUR and it is credited against a later package.",
        "links": [("/free.html", "Brazilian vs European Portuguese"),
                  ("/pricing.html", "Lessons and prices"),
                  ("/about.html", "About Renata")],
        "seeds": [
            {"q": "how to say hello in brazilian portuguese", "angle": "oi vs ola vs e ai: what a Brazilian actually says, by time of day and by how formal the situation is"},
            {"q": "how to say thank you in brazilian portuguese", "angle": "obrigado vs obrigada agrees with the speaker, not the listener, which is the mistake almost every course glosses over"},
            {"q": "how are you in brazilian portuguese", "angle": "tudo bem, tudo bom, como vai, and the fact that tudo bem is both the question and the answer"},
            {"q": "tu vs voce in brazilian portuguese", "angle": "regional reality: voce almost everywhere, tu in the south and northeast, and why tu is usually conjugated as voce anyway"},
            {"q": "how to say i love you in brazilian portuguese", "angle": "eu te amo vs eu te adoro vs gosto de voce, and the weight each one carries"},
            {"q": "brazilian portuguese pronunciation for english speakers", "angle": "the sounds that actually block beginners: nasal vowels, the de and te palatalisation, and the r that is really an h"},
            {"q": "how to say good morning in brazilian portuguese", "angle": "bom dia, boa tarde, boa noite, and where the cut-off really sits in Brazil"},
            {"q": "brazilian portuguese slang for beginners", "angle": "the ten words you hear in the first week: legal, massa, cara, valeu, beleza, with register warnings"},
            {"q": "numbers in brazilian portuguese", "angle": "counting, prices, phone numbers, and the meia for six that confuses every learner"},
            {"q": "how to introduce yourself in brazilian portuguese", "angle": "a short script a beginner can use on day one, including the ser vs estar trap"},
            {"q": "false friends in portuguese for english speakers", "angle": "pretender, esquisito, puxar, empurrar, and the ones that cause real embarrassment"},
            {"q": "ser vs estar in brazilian portuguese", "angle": "the permanent versus temporary rule is not enough, so show the cases where it breaks"},
            {"q": "how to order food in brazilian portuguese", "angle": "a real restaurant sequence in Brazil, including por quilo places and asking for the bill"},
            {"q": "is brazilian portuguese hard to learn for english speakers", "angle": "an honest answer about what is genuinely easy and what is genuinely hard, with no motivational filler"},
            {"q": "how long does it take to learn brazilian portuguese", "angle": "honest hour estimates per level and what one weekly evening lesson realistically gets you"},
            {"q": "brazilian portuguese greetings and goodbyes", "angle": "tchau, ate mais, falou, and the kiss-on-the-cheek count by region"},
            {"q": "how to say sorry in brazilian portuguese", "angle": "desculpa vs desculpe vs perdao vs me perdoa and how heavy each one is"},
            {"q": "days of the week in brazilian portuguese", "angle": "the segunda-feira logic, why there is no Monday word, and how Brazilians shorten them"},
            {"q": "brazilian portuguese verb conjugation for beginners", "angle": "the three present-tense patterns and the shortcut of learning eu, voce and a gente first"},
            {"q": "how to say yes and no politely in brazilian portuguese", "angle": "Brazilians rarely say a bare nao, so show the softening patterns"},
            {"q": "portuguese words that do not exist in english", "angle": "saudade, cafune, jeitinho, with accurate and non-romanticised explanations"},
            {"q": "how to talk about prices and money in brazilian portuguese", "angle": "reais, centavos, saying prices out loud, and pix vocabulary"},
            {"q": "brazilian portuguese for travel to brazil", "angle": "airport, taxi, hotel, pharmacy: a compact survival set"},
            {"q": "a gente vs nos in brazilian portuguese", "angle": "why a gente takes the third person singular and why beginners should learn it first"},
            {"q": "how to say goodbye in brazilian portuguese", "angle": "tchau, ate logo, ate mais, falou, and which one closes a work call versus a night out"},
            {"q": "how to say please in brazilian portuguese", "angle": "por favor, faz favor, and the fact that Brazilians soften with tone rather than with the word"},
            {"q": "how to say my name is in brazilian portuguese", "angle": "meu nome e versus eu me chamo versus sou o, with the register difference"},
            {"q": "how to say what time is it in brazilian portuguese", "angle": "asking and understanding the answer, the 24 hour clock, and meia for thirty"},
            {"q": "how to say excuse me in brazilian portuguese", "angle": "com licenca to pass, desculpa to apologise, and why swapping them sounds odd"},
            {"q": "how to say beautiful in brazilian portuguese", "angle": "lindo, bonito, gato, and how compliments actually land in Brazil"},
            {"q": "how to say friend in brazilian portuguese", "angle": "amigo, cara, mano, brother, and the age and region each one signals"},
            {"q": "how to say water and coffee in brazilian portuguese", "angle": "ordering drinks, cafezinho culture, and agua com gas versus sem gas"},
            {"q": "how to say happy birthday in brazilian portuguese", "angle": "parabens, the birthday song, and what people actually write in a message"},
            {"q": "how to say good luck in brazilian portuguese", "angle": "boa sorte, merda in theatre, and the superstition behind it"},
            {"q": "how to say i do not understand in brazilian portuguese", "angle": "the four phrases that get you unstuck in a real conversation"},
            {"q": "how to say how much does it cost in brazilian portuguese", "angle": "quanto custa, quanto e, and haggling language at a feira"},
            {"q": "how to say i am hungry in brazilian portuguese", "angle": "estou com fome and why estar com beats ter for physical states"},
            {"q": "months and dates in brazilian portuguese", "angle": "lowercase months, day-before-month order, and saying a date out loud"},
            {"q": "colours in brazilian portuguese", "angle": "the list plus gender agreement, and the colours that do not agree"},
            {"q": "family words in brazilian portuguese", "angle": "pai, mae, irmao, and the diminutives Brazilians actually use at home"},
            {"q": "brazilian portuguese diminutives explained", "angle": "the -inho suffix does far more than make things small: affection, softening, sarcasm"},
            {"q": "how to write a message in brazilian portuguese", "angle": "WhatsApp register, abbreviations like vc, tb, blz, and where they are inappropriate"},
            {"q": "brazilian portuguese accent marks explained", "angle": "til, agudo, circunflexo, cedilha: what each one changes about the sound"},
            {"q": "por vs para in brazilian portuguese", "angle": "the split that trips up every Spanish and English speaker, with a usable test"},
            {"q": "how to say i am learning portuguese", "angle": "the sentence you will say most in your first month, plus the replies you will get"},
        ],
    },
    "china": {
        "dir": "china",
        "lang": "pt-BR",
        "og_locale": "pt_BR",
        "eyebrow_default": "Importar da China",
        "hub_title": "Importar da China sem se perder no idioma",
        "hub_desc": "Guias praticos em portugues sobre como falar com fornecedor chines: mensagens, negociacao, 1688 e Alibaba, e o que a resposta realmente significa.",
        "hub_h1": "Falar com fornecedor chines, em portugues",
        "hub_intro": "Quem importa da China quase nunca perde dinheiro por causa do preco. Perde por causa de uma frase mal entendida. Estes guias sao escritos por uma brasileira que estudou na China e acompanha conversas com fornecedores ao vivo.",
        "hub_cta_label": "Falar no WhatsApp",
        "hub_cta_href": "https://wa.me/351935328206",
        "hub_back": "Traducao chines-portugues",
        "covered": ["traducao chines portugues", "traducao portugues chines", "tradutor online",
                    "google tradutor"],
        "wa_text": "Oi%20Renata%2C%20preciso%20de%20ajuda%20para%20falar%20com%20um%20fornecedor%20chines.",
        "cta_title": "Precisa de alguem na conversa?",
        "cta_body": "A Renata entra na chamada com voce e o fornecedor, le e escreve as mensagens em chines e explica o que a resposta realmente quer dizer. A distancia: 25 EUR por meia hora. Ela nao e agente de compras e nao faz traducao juramentada.",
        "links": [("/traducao-chines-portugues.html", "Onde a traducao automatica PT-ZH falha"),
                  ("/interpreting.html", "Interpreting and message support"),
                  ("/about.html", "Quem e a Renata")],
        "seeds": [
            {"q": "como importar da china pela primeira vez", "angle": "o passo a passo real, com os pontos em que o idioma trava a negociacao e nao o preco"},
            {"q": "como falar com fornecedor chines", "angle": "o que escrever na primeira mensagem, por que respostas curtas nao sao grosseria, e o fuso horario que decide a velocidade"},
            {"q": "1688 ou alibaba qual usar", "angle": "o 1688 e o mercado interno chines, so em chines e sem intermediario em ingles: a diferenca pratica de preco e de risco"},
            {"q": "como comprar no 1688 sem falar chines", "angle": "navegar a plataforma, ler a ficha do produto, e onde a traducao automatica engana"},
            {"q": "o que significa moq e como negociar", "angle": "MOQ, amostra e preco por faixa, com as frases exatas em chines que abrem espaco"},
            {"q": "frases em chines para negociar com fornecedor", "angle": "uma tabela de frases operacionais com pinyin e o efeito que cada uma provoca do outro lado"},
            {"q": "ano novo chines afeta minha entrega", "angle": "a fabrica fecha semanas e nao dias: como planejar o pedido e o que perguntar antes"},
            {"q": "como saber se o fornecedor chines e confiavel", "angle": "sinais verificaveis na propria plataforma e o que uma chamada de video revela em cinco minutos"},
            {"q": "como pedir amostra para fornecedor chines", "angle": "quem paga o que, o que combinar por escrito, e a frase que evita receber um produto diferente"},
            {"q": "erros de traducao que custam dinheiro na importacao", "angle": "casos concretos de cor, material, embalagem e prazo, onde o portugues e o chines nao se encaixam"},
            {"q": "como negociar preco com fornecedor chines", "angle": "por que pedir desconto direto costuma travar, e o que funciona: volume, prazo e especificacao"},
            {"q": "diferenca entre alibaba aliexpress e 1688", "angle": "publico, preco, quantidade minima e em que idioma cada um funciona de verdade"},
            {"q": "o que perguntar antes de fechar pedido com a china", "angle": "uma checklist de perguntas que precisam de resposta por escrito"},
            {"q": "como usar wechat para falar com fornecedor", "angle": "por que o fornecedor prefere WeChat, como funciona na pratica e o que muda no tom"},
            {"q": "como ler ficha de produto em chines", "angle": "os caracteres que aparecem sempre e o que significam para o pedido"},
            {"q": "dropshipping da china vale a pena", "angle": "resposta honesta sobre prazo, qualidade e a parte que ninguem conta: a comunicacao pos-venda"},
            {"q": "como resolver problema com pedido da china", "angle": "produto errado, atraso ou quantidade a menos: como escrever a reclamacao para ela ser resolvida"},
            {"q": "intermediario ou comprar direto da china", "angle": "o que um intermediario realmente resolve, o que ele nao resolve, e quando falar direto sai melhor"},
            {"q": "como importar roupas da china", "angle": "tabela de medidas chinesa, tecido, e as palavras que mudam o produto que chega"},
            {"q": "incoterms explicados para quem importa da china", "angle": "EXW, FOB e CIF em portugues simples e o que cada um significa na conversa com o fornecedor"},
        ],
    },
    # Engelstalige tak voor Europese dropshippers en webshops. Eerlijke verwachting, gemeten
    # 19-08-2026 in de eigen MCC: dit cluster is klein (0-170/mnd per term, tegen 2900 voor
    # `como importar da china` in het Portugees). Deze silo is er dus voor outreach en voor de
    # lange staart, niet omdat er een grote zoekmarkt op wacht. Nooit als sourcing agent
    # positioneren - dat is precies wat Renata niet is en wat de poort blokkeert.
    "sourcing": {
        "dir": "sourcing",
        "lang": "en",
        "og_locale": "en_GB",
        "eyebrow_default": "Buying from China",
        "hub_title": "Talking to Chinese suppliers, in plain English",
        "hub_desc": "Practical guides for European sellers buying from China: what your supplier actually meant, and how to write a message that gets a straight answer.",
        "hub_h1": "What your Chinese supplier actually meant",
        "hub_intro": "Most orders from China do not go wrong on price. They go wrong on one sentence that both sides read differently. These guides come from someone who studied in China and sits in on those calls.",
        "hub_cta_label": "Ask Renata on WhatsApp",
        "hub_cta_href": "https://wa.me/351935328206",
        "hub_back": "Interpreting",
        "covered": ["chinese interpreter lisbon", "interpreting price"],
        "wa_text": "Hi%20Renata%2C%20I%20need%20help%20talking%20to%20a%20Chinese%20supplier.",
        "cta_title": "Want someone on the call?",
        "cta_body": "Renata joins the call with you and your supplier, reads and writes the Chinese messages, and tells you what the answer really meant. Remote it is 25 EUR per half hour. She is not a sourcing agent and does not do sworn translation.",
        "links": [("/interpreting.html", "Interpreting and message support"),
                  ("/china/", "Em portugues: importar da China"),
                  ("/about.html", "About Renata")],
        "seeds": [
            {"q": "how to message a chinese supplier on alibaba", "angle": "a copy-paste first message that gets a real quote back, and the four things it must contain"},
            {"q": "what does moq mean and how to negotiate it", "angle": "why a supplier quotes a high MOQ first, and the specific asks that move it"},
            {"q": "1688 vs alibaba for european sellers", "angle": "1688 is the Chinese domestic market, Chinese only, no English middle layer: what that changes about price and risk"},
            {"q": "how to buy from 1688 without a chinese address", "angle": "the practical blockers and the honest workarounds, including what an agent is genuinely for"},
            {"q": "chinese new year shipping delays explained", "angle": "factories close for weeks not days, so plan backwards and ask these questions in November"},
            {"q": "how to tell if a chinese supplier is trustworthy", "angle": "verifiable signals on the platform itself, and what a five minute video call reveals"},
            {"q": "chinese phrases for talking to suppliers", "angle": "an operational phrase table with characters, pinyin and what each one triggers on the other side"},
            {"q": "why chinese suppliers reply with short messages", "angle": "register, time zones and the WeChat habit: short is not rude, and what it means for your follow-up"},
            {"q": "how to ask a chinese supplier for a sample", "angle": "who pays for what, what to confirm in writing, and the sentence that stops a different product arriving"},
            {"q": "translation mistakes that cost money when importing", "angle": "concrete cases in colour, material, packaging and lead time where English and Chinese do not line up"},
            {"q": "how to negotiate price with a chinese supplier", "angle": "asking for a discount outright usually stalls, so trade on volume, lead time and specification instead"},
            {"q": "alibaba vs aliexpress vs 1688 for dropshipping", "angle": "audience, price, minimum quantity and which language each one really runs in"},
            {"q": "questions to ask before placing a china order", "angle": "a checklist of questions that need an answer in writing, not on a call"},
            {"q": "how to use wechat with a chinese supplier", "angle": "why the supplier prefers WeChat, how it works in practice and how the tone shifts"},
            {"q": "how to read a chinese product listing", "angle": "the characters that always appear and what each one means for your order"},
            {"q": "what to do when a china order arrives wrong", "angle": "wrong product, late, short quantity: how to write the complaint so it actually gets resolved"},
            {"q": "incoterms explained for small importers", "angle": "EXW, FOB and CIF in plain English and what each means in the conversation with the supplier"},
            {"q": "do i need a sourcing agent to buy from china", "angle": "an honest breakdown of what an agent solves, what it does not, and when talking directly works better"},
        ],
    },
}


# ---------------------------------------------------------------- helpers
def slugify(text):
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    return re.sub(r"-{2,}", "-", t)[:70]


def _extract_json(raw):
    s = (raw or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-z]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    i, j = s.find("{"), s.rfind("}")
    if i == -1 or j == -1:
        raise ValueError("geen JSON in LLM-antwoord")
    return json.loads(s[i:j + 1])


def llm_json(system, user, max_tokens=4600):
    for attempt in range(2):
        extra = "" if attempt == 0 else "\n\nReturn ONLY the JSON object. No prose, no code fences."
        out = chat([{"role": "system", "content": system + extra},
                    {"role": "user", "content": user}],
                   temperature=0.55, max_tokens=max_tokens)
        try:
            return _extract_json(out)
        except Exception:  # noqa: BLE001
            if attempt == 1:
                raise
    return None


def manifest_path(silo):
    return SITE_DIR / SILOS[silo]["dir"] / "manifest.json"


def load_manifest(silo):
    p = manifest_path(silo)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"pages": []}


def save_manifest(silo, m):
    p = manifest_path(silo)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")


def _norm(text):
    t = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9\s]", " ", t.lower())


def shingles(text, n=3):
    words = _norm(text).split()
    return {" ".join(words[i:i + n]) for i in range(max(0, len(words) - n + 1))}


def jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# ---------------------------------------------------------------- topic-keuze
# Portugese vraagvormen en woorden die een query in de pt-BR-silo thuisbrengen.
PT_MARKERS = ("como ", "qual ", "quanto ", "fornecedor", "chines", "importar", "importacao",
              "comprar", "preco", "onde ", " que ", "para o brasil")
# Engelse onderwerpen die over inkoop uit China gaan horen in /sourcing/, niet in /learn/.
SOURCING_MARKERS = ("supplier", "china", "chinese", "alibaba", "1688", "aliexpress", "moq",
                    "sourcing", "import", "shipping", "factory", "wechat", "dropship")


def route_silo(query_text):
    """Bij welke silo hoort deze GSC-query? None = bij geen enkele, dus overslaan."""
    low = _norm(query_text)
    if any(m in low for m in PT_MARKERS):
        return "china"
    if any(m in low for m in SOURCING_MARKERS):
        return "sourcing"
    if "portuguese" in low or "brazil" in low:
        return "learn"
    return None


def gsc_striking(token, site, silo):
    """Queries op pos 4-20 met minstens 2 vertoningen. Deze site is jong: de drempel moet laag,
    anders levert de pijplijn structureel nul onderwerpen (les van acinstallationlisbon)."""
    end = _dt.date.today() - _dt.timedelta(days=2)
    start = end - _dt.timedelta(days=90)
    rep, err = query(token, site, start.isoformat(), end.isoformat(),
                     dimensions=["query"], row_limit=1000)
    if err:
        return [], err
    cfg = SILOS[silo]
    out = []
    for r in parse_rows(rep or {}):
        q = (r["key"] or "").strip()
        if not q or r["impressions"] < 2:
            continue
        if not (3.5 < r["position"] <= 20.0):
            continue
        low = _norm(q)
        if any(_norm(term) in low for term in cfg["covered"]):
            continue
        if len(low.split()) < 3:
            continue  # head-term: daar moeten de dienstpaginas op ranken, niet een LP
        if route_silo(q) != silo:
            continue  # hoort in een andere silo (of nergens)
        out.append({"q": q, "impressions": r["impressions"], "position": r["position"]})
    out.sort(key=lambda x: -x["impressions"])
    return out, None


def build_queue(token, site, silo, manifest):
    done_q = {_norm(p["target_query"]) for p in manifest["pages"]}
    done_slug = {p["slug"] for p in manifest["pages"]}
    queue = []
    if token and site:
        gsc, _ = gsc_striking(token, site, silo)
        for g in gsc:
            if _norm(g["q"]) in done_q or slugify(g["q"]) in done_slug:
                continue
            queue.append({"q": g["q"], "angle": "", "source": "gsc", "silo": silo,
                          "impressions": g["impressions"], "position": g["position"]})
    for s in SILOS[silo]["seeds"]:
        if _norm(s["q"]) in done_q or slugify(s["q"]) in done_slug:
            continue
        queue.append({"q": s["q"], "angle": s["angle"], "source": "seed", "silo": silo,
                      "impressions": 0, "position": 0})
    return queue


# ---------------------------------------------------------------- generatie
SYSTEM_LEARN = (
    "You are Renata, a Brazilian Portuguese teacher writing short, genuinely useful guides for English "
    "speakers who are learning Brazilian Portuguese. You write in clear, warm, direct English, first hand. "
    "Every Portuguese example must be BRAZILIAN Portuguese and must be correct: correct spelling, correct "
    "accents, correct gender agreement. Where a generic Portuguese answer found online would be European "
    "Portuguese and therefore wrong or unnatural in Brazil, say so explicitly, because that contrast is the "
    "whole point of the page. Never fabricate statistics, student names, testimonials, reviews or results. "
    "Avoid AI cliches, filler and hype. Output MUST be one valid JSON object and nothing else."
)

SYSTEM_CHINA = (
    "Voce escreve em PORTUGUES DO BRASIL (pt-BR) para quem importa ou revende produtos da China: "
    "pequenos importadores, lojistas e quem faz dropshipping. O tom e direto, pratico e honesto, sem hype "
    "e sem promessa de lucro. Quem assina e a Renata: brasileira, estudou na Universidade de Hubei, HSK 4, "
    "mora perto de Lisboa e acompanha conversas com fornecedores chineses ao vivo. Sempre que citar chines, "
    "escreva os caracteres corretos, o pinyin entre parenteses e a traducao. Nunca invente estatistica, nome "
    "de cliente, depoimento, avaliacao ou caso de sucesso. Nunca prometa resultado, nem sugira que a Renata "
    "faz inspecao de fabrica, despacho aduaneiro ou traducao juramentada. Saida DEVE ser um unico objeto "
    "JSON valido e nada mais."
)

USER_TMPL = """Write one landing page that fully answers this real search query:
  "{query}"
{angle}
{facts}

Return a JSON object with EXACTLY these keys:
{{
 "title": "SEO title tag, max 58 characters, contains the core of the query, natural, no brand suffix",
 "meta_description": "max 150 characters, answers the query and gives a reason to read on",
 "slug": "kebab-case, ascii only, no accents, derived from the query",
 "eyebrow": "short kicker label, max 32 chars",
 "h1": "H1 as a clear question or promise (plain text, no HTML)",
 "h1_highlight": "a 2-4 word phrase that appears verbatim inside h1 (or empty string)",
 "lead": "opening paragraph, 40-70 words. The FIRST SENTENCE must answer the query directly in one line, because that line is what an AI assistant will quote. Plain text.",
 "sections": [
   {{"h2":"section heading",
     "body":["paragraph 1 (2-4 sentences)","paragraph 2 (2-4 sentences)"],
     "bullets":["optional short bullet","optional short bullet"],
     "table": {{"head":["col a","col b","col c"],"rows":[["...","...","..."]]}} }}
 ],
 "faqs":[{{"q":"question","a":"answer, 2-4 sentences"}}]
}}

Rules:
- 4 to 6 sections. Each section: an h2 plus 2 short paragraphs.
- Add "bullets" only where a list genuinely helps. Add "table" ONLY where a phrase table or a comparison
  genuinely helps, at most 2 tables on the page, exactly 3 columns, 4 to 10 rows. Otherwise omit the key.
- EXACTLY 6 faqs, each a distinct real question about this topic.
- Total body 800-1300 words. No HTML, no markdown, no links inside any field, plain text only.
- {lang_rule}
- Every factual claim about Renata, her prices, her level or her services must come from the facts above.
  If something is not in the facts, do not say it."""

SYSTEM_SOURCING = (
    "You write in clear, plain English for European e-commerce sellers, small importers and "
    "dropshippers who buy from China. The tone is direct, practical and honest, with no hype and no "
    "profit promises. The author is Renata: Brazilian, studied at Hubei University, HSK 4, based near "
    "Lisbon, and she sits in on live calls with Chinese suppliers. Whenever you quote Chinese, give the "
    "correct characters, the pinyin in brackets and the translation. Never invent statistics, client "
    "names, testimonials, reviews or case studies. Never promise an outcome, and never suggest Renata "
    "does factory inspection, customs clearance or sworn translation. You may explain what those things "
    "are as part of the import process, as long as it is clear they are not her service. Output MUST be "
    "one valid JSON object and nothing else."
)

LANG_RULE = {
    "learn": "Write the page in English. Portuguese examples must be Brazilian Portuguese, spelled correctly with accents.",
    "china": "Escreva a pagina inteira em portugues do Brasil. Termos chineses com caracteres, pinyin e traducao.",
    "sourcing": "Write the page in English. Chinese terms with characters, pinyin and translation.",
}

SYSTEMS = {"learn": SYSTEM_LEARN, "china": SYSTEM_CHINA, "sourcing": SYSTEM_SOURCING}


def generate(topic):
    silo = topic["silo"]
    system = SYSTEMS[silo]
    angle = "Editorial angle to cover: " + topic["angle"] + "\n" if topic.get("angle") else ""
    user = USER_TMPL.format(query=topic["q"], angle=angle, facts=RENATA_FACTS,
                            lang_rule=LANG_RULE[silo])
    art = llm_json(system, user)
    art["target_query"] = topic["q"]
    art["source"] = topic["source"]
    art["silo"] = silo
    return art


def repair(art):
    """Redt goede content van harde afkeuring: knipt alleen te lange titel/meta op woordgrens."""
    def trim(s, n):
        s = re.sub(r"\s+", " ", (s or "").strip())
        if len(s) <= n:
            return s
        cut = s[:n]
        if " " in cut:
            cut = cut[:cut.rfind(" ")]
        return cut.rstrip(" ,;:-–—·.")
    if art.get("title"):
        art["title"] = trim(art["title"], 60)
    if art.get("meta_description"):
        art["meta_description"] = trim(art["meta_description"], 155)
    return art


# ---------------------------------------------------------------- kwaliteitspoort
STOPWORDS = {"the", "and", "for", "with", "how", "what", "your", "you", "does", "did", "are", "into",
             "say", "que", "com", "para", "uma", "por", "dos", "das", "meu", "minha", "como", "isso"}

ALLOWED_PRICES = {"45", "65", "300", "89", "69", "279", "229", "25"}


def gate(art, manifest):
    reasons = []
    title = (art.get("title") or "").strip()
    desc = (art.get("meta_description") or "").strip()
    slug = slugify(art.get("slug") or art.get("target_query", ""))
    art["slug"] = slug
    h1 = (art.get("h1") or "").strip()
    lead = (art.get("lead") or "").strip()
    secs = art.get("sections") or []
    faqs = art.get("faqs") or []

    if not (10 <= len(title) <= 60):
        reasons.append("titel-lengte " + str(len(title)) + " (moet 10-60)")
    if not (50 <= len(desc) <= 155):
        reasons.append("meta-lengte " + str(len(desc)) + " (moet 50-155)")
    if not slug:
        reasons.append("lege slug")
    if slug in {p["slug"] for p in manifest["pages"]}:
        reasons.append("slug " + slug + " bestaat al")
    if len(h1) < 12:
        reasons.append("h1 te kort")
    if not (20 <= len(lead) <= 700):
        reasons.append("lead-lengte " + str(len(lead)))
    if not (4 <= len(secs) <= 6):
        reasons.append(str(len(secs)) + " secties (moet 4-6)")
    if len(faqs) != 6:
        reasons.append(str(len(faqs)) + " faqs (moet 6)")

    tbl_txt = []
    for s in secs:
        t = s.get("table") or {}
        for row in (t.get("rows") or []):
            tbl_txt.extend(str(c) for c in row)
    body_text = " ".join([lead]
                         + [p for s in secs for p in (s.get("body") or [])]
                         + [b for s in secs for b in (s.get("bullets") or [])]
                         + tbl_txt
                         + [f.get("a", "") for f in faqs])
    words = len(body_text.split())
    if words < 650:
        reasons.append("maar " + str(words) + " woorden (min 650)")

    low = _norm(title + " " + desc + " " + body_text + " " + " ".join(f.get("q", "") for f in faqs))
    for bad in FORBIDDEN_HARD:
        nb = _norm(bad).strip()
        if nb and nb in low:
            reasons.append("verboden claim aanwezig: " + bad)
    # Kwalificatie-claims: altijd een ontkenning vereist.
    for bad in FORBIDDEN_CREDENTIAL:
        nb = _norm(bad).strip()
        if not nb:
            continue
        for m in re.finditer(re.escape(nb), low):
            if not _negated_before(low, m.start()):
                reasons.append("kwalificatie-claim zonder ontkenning: " + bad)
                break
    # Diensttermen: neutrale vakuitleg mag; gekoppeld aan Renata of "wij" moet er ontkend worden.
    for bad in FORBIDDEN_SERVICE:
        nb = _norm(bad).strip()
        if not nb:
            continue
        for m in re.finditer(re.escape(nb), low):
            if _about_renata(low, m.start(), len(nb)) and not _negated_before(low, m.start()):
                reasons.append("dienst aan Renata toegeschreven: " + bad)
                break
    # Titel en meta mogen deze termen sowieso niet dragen: daar past geen ontkenning in beeld.
    head_low = _norm(title + " " + desc)
    for bad in FORBIDDEN_CREDENTIAL + FORBIDDEN_SERVICE:
        nb = _norm(bad).strip()
        if nb and nb in head_low:
            reasons.append("verboden term in titel/meta: " + bad)

    # Prijzen: elk eurobedrag moet uit de tariefkaart komen.
    for m in re.finditer(r"(\d{1,4})\s*(?:eur|euros?|€)|€\s*(\d{1,4})", body_text.lower()):
        val = m.group(1) or m.group(2)
        if val not in ALLOWED_PRICES:
            reasons.append("prijs buiten de tariefkaart: " + val + " EUR")
            break

    # Telefoonnummers: alleen het WhatsApp-nummer mag voorkomen. `in` en niet `==`, want het model
    # plakt er soms een openingstijd of jaartal tegenaan ("+351 935 328 206 08") en dat is geen fout.
    for m in re.finditer(r"\+?\d[\d\s\-()]{7,}\d", body_text):
        digits = re.sub(r"\D", "", m.group(0))
        if len(digits) < 9 or "935328206" in digits:
            continue
        reasons.append("onbekend telefoonnummer in tekst: " + m.group(0).strip()[:20])
        break

    # GEO-relevantie: minstens 1 betekenisvol querywoord in de h1.
    qwords = [w for w in _norm(art["target_query"]).split() if len(w) > 3 and w not in STOPWORDS]
    if qwords and not any(w in _norm(h1) for w in qwords):
        reasons.append("query-kernwoord niet in h1 (GEO-relevantie)")

    # Dedup binnen dezelfde silo.
    new_sh = shingles(body_text)
    worst = 0.0
    for p in manifest["pages"]:
        worst = max(worst, jaccard(new_sh, shingles(p.get("text", ""))))
    if worst >= 0.42:
        reasons.append("te lijkend op bestaande pagina (Jaccard " + ("%.2f" % worst) + " >= 0.42)")

    art["_body_text"] = body_text
    art["_words"] = words
    return (len(reasons) == 0), reasons


# ---------------------------------------------------------------- render
def esc(s):
    return _html.escape(str(s or ""), quote=True)


def _bold_first_sentence(lead):
    lead = (lead or "").strip()
    m = re.search(r"(?<=[.!?])\s", lead)
    if not m:
        return "<strong>" + esc(lead) + "</strong>"
    return "<strong>" + esc(lead[:m.start() + 1]) + "</strong> " + esc(lead[m.end():])


def _table_html(t):
    head = t.get("head") or []
    rows = t.get("rows") or []
    if not head or not rows:
        return ""
    ths = "".join("<th scope=\"col\">" + esc(h) + "</th>" for h in head)
    trs = []
    for row in rows:
        tds = "".join(
            "<td data-label=\"" + esc(head[i] if i < len(head) else "") + "\">" + esc(c) + "</td>"
            for i, c in enumerate(row))
        trs.append("<tr>" + tds + "</tr>")
    return ("\n            <table class=\"tabel-responsive\">\n"
            "              <thead><tr>" + ths + "</tr></thead>\n"
            "              <tbody>" + "".join(trs) + "</tbody>\n"
            "            </table>")


def _sections_html(secs):
    out = []
    tones = ["bg-white", "bg-[#f2eadb]"]
    for i, s in enumerate(secs):
        tone = tones[i % 2]
        paras = "".join("\n            <p class=\"prose mt-4\">" + esc(p) + "</p>"
                        for p in (s.get("body") or []))
        bl = s.get("bullets") or []
        bullets = ""
        if bl:
            lis = "".join("\n              <li>" + esc(b) + "</li>" for b in bl)
            bullets = "\n            <ul class=\"prose mt-5 list-disc pl-5\">" + lis + "\n            </ul>"
        table = _table_html(s.get("table") or {})
        out.append(
            "\n      <section class=\"reveal section-pad " + tone + "\">\n"
            "        <div class=\"wrap\">\n"
            "          <div class=\"max-w-3xl\">\n"
            "            <h2 class=\"font-display text-2xl font-bold md:text-3xl\">" + esc(s.get("h2", "")) + "</h2>"
            + paras + bullets + table +
            "\n          </div>\n        </div>\n      </section>")
    return "".join(out)


def _faq_visible(faqs):
    items = []
    for f in faqs:
        items.append(
            "\n            <div class=\"path-card p-6\">\n"
            "              <h3 class=\"font-display text-lg font-bold\">" + esc(f.get("q", "")) + "</h3>\n"
            "              <p class=\"prose mt-3\">" + esc(f.get("a", "")) + "</p>\n"
            "            </div>")
    return "".join(items)


def _related_html(silo, manifest, current_slug, limit=4):
    cfg = SILOS[silo]
    rel = [p for p in reversed(manifest["pages"]) if p["slug"] != current_slug][:limit]
    items = []
    for p in rel:
        items.append(
            "\n            <a class=\"path-card p-6\" href=\"/" + cfg["dir"] + "/" + esc(p["slug"]) + "/\">\n"
            "              <p class=\"eyebrow\">" + esc(p.get("eyebrow", cfg["eyebrow_default"])) + "</p>\n"
            "              <p class=\"font-display mt-2 text-lg font-bold\">" + esc(p["title"]) + "</p>\n"
            "            </a>")
    for href, label in cfg["links"]:
        if len(items) >= limit + 2:
            break
        items.append(
            "\n            <a class=\"path-card p-6\" href=\"" + href + "\">\n"
            "              <p class=\"eyebrow\">" + esc(cfg["hub_back"]) + "</p>\n"
            "              <p class=\"font-display mt-2 text-lg font-bold\">" + esc(label) + "</p>\n"
            "            </a>")
    return "".join(items)


def _graph_json(art, canonical, pubdate, silo):
    cfg = SILOS[silo]
    graph = [
        {"@type": "Article",
         "@id": canonical + "#article",
         "headline": art["h1"][:110],
         "description": art["meta_description"],
         "inLanguage": cfg["lang"],
         "datePublished": pubdate,
         "dateModified": pubdate,
         "author": {"@id": BASE_URL + "/about.html#renata"},
         "publisher": {"@id": BASE_URL + "/about.html#renata"},
         "isPartOf": {"@id": BASE_URL + "/" + cfg["dir"] + "/#hub"},
         "mainEntityOfPage": canonical},
        {"@type": "FAQPage",
         "@id": canonical + "#faq",
         "inLanguage": cfg["lang"],
         "mainEntity": [{"@type": "Question", "name": f.get("q", ""),
                         "acceptedAnswer": {"@type": "Answer", "text": f.get("a", "")}}
                        for f in art["faqs"]]},
        {"@type": "BreadcrumbList",
         "@id": canonical + "#crumbs",
         "itemListElement": [
             {"@type": "ListItem", "position": 1, "name": "Home", "item": BASE_URL + "/"},
             {"@type": "ListItem", "position": 2, "name": cfg["hub_h1"],
              "item": BASE_URL + "/" + cfg["dir"] + "/"},
             {"@type": "ListItem", "position": 3, "name": art["h1"][:80], "item": canonical}]},
    ]
    return json.dumps({"@context": "https://schema.org", "@graph": graph},
                      ensure_ascii=False, indent=2)


HEAD_TMPL = """<!doctype html>
<html lang="@@LANG@@">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>@@TITLE@@</title>
    <meta name="description" content="@@DESC@@">
    <link rel="canonical" href="@@CANONICAL@@">
    <link rel="alternate" hreflang="@@LANG@@" href="@@CANONICAL@@">
    <link rel="icon" href="/favicon.ico" sizes="48x48 96x96">
    <link rel="icon" href="/favicon-96.png" type="image/png" sizes="96x96">
    <link rel="icon" href="/images/logo-mark.svg" type="image/svg+xml">
    <link rel="apple-touch-icon" href="/apple-touch-icon.png">
    <meta name="theme-color" content="#fffaf2">
    <meta property="og:title" content="@@OGTITLE@@">
    <meta property="og:description" content="@@DESC@@">
    <meta property="og:type" content="article">
    <meta property="og:locale" content="@@OGLOCALE@@">
    <meta property="og:url" content="@@CANONICAL@@">
    <meta property="og:image" content="https://portuguesewithrenata.com/images/renata-headshot.webp">
    <meta name="twitter:card" content="summary_large_image">
    <script async src="https://www.googletagmanager.com/gtag/js?id=@@GA4@@"></script>
    <script>
      window.dataLayer = window.dataLayer || [];
      function gtag(){dataLayer.push(arguments);}
      gtag('js', new Date());
      gtag('config', '@@GA4@@');
    </script>
    <script src="https://unpkg.com/lucide@latest/dist/umd/lucide.min.js" defer></script>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600;9..144,750&family=Hanken+Grotesk:wght@400;600;700;800;900&family=Noto+Serif+SC:wght@500;700;900&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="/assets/styles.css">
    <script type="application/ld+json">
@@JSONLD@@
    </script>
  </head>
  <body>
    <header class="site-header">
      <div class="wrap header-row">
        <a class="site-brand" href="/">
          <img src="/images/logo-mark.svg" width="48" height="48" alt="" aria-hidden="true">
          <span>Portuguese With Renata</span>
        </a>
        <button class="menu-toggle" type="button" data-menu-toggle aria-expanded="false" aria-controls="site-nav" aria-label="Open menu">
          <span class="menu-toggle-lines" aria-hidden="true"></span>
        </button>
        <nav class="site-nav" id="site-nav" data-site-nav aria-label="Main navigation">
          <a class="nav-link" href="/#why">Why Brazilian</a>
          <a class="nav-link" href="/pricing.html">Lessons &amp; prices</a>
          <a class="nav-link" href="/learn/">Learn</a>
          <a class="nav-link" href="/sourcing/">Suppliers</a>
          <a class="nav-link" href="/about.html">About Renata</a>
          <a class="btn btn-whatsapp" href="https://wa.me/@@WA@@"><i data-lucide="message-circle" class="h-4 w-4"></i>WhatsApp</a>
        </nav>
      </div>
    </header>
"""

FOOT_TMPL = """
    <footer class="bg-[#171f1d] py-10 text-white">
      <div class="wrap flex flex-col gap-5 md:flex-row md:items-start md:justify-between">
        <div>
          <p class="font-display text-xl font-bold">Portuguese With Renata</p>
          <p class="footer-meta mt-2">Brazilian Portuguese, taught online<br>Based in Oeiras, Portugal<br>Website by <a href="https://adsvantage.pt/" target="_blank" rel="noopener">Adsvantage.pt</a></p>
        </div>
        <nav class="flex flex-wrap gap-5 text-sm font-bold text-white/70" aria-label="Footer navigation">
          <a href="/#why">Why Brazilian Portuguese</a>
          <a href="/pricing.html">Lessons &amp; prices</a>
          <a href="/learn/">Learn Brazilian Portuguese</a>
          <a href="/sourcing/">Chinese suppliers</a>
          <a href="/china/">Importar da China (PT)</a>
          <a href="/about.html">About Renata</a>
          <a href="/interpreting.html">Chinese interpreting</a>
          <a class="font-zh" href="/zh.html">中文</a>
        </nav>
      </div>
    </footer>

    <script src="/assets/site.js" defer></script>
    <script>
      window.addEventListener("load", () => window.lucide?.createIcons());
    </script>
    <a class="wa-float" href="https://wa.me/@@WA@@?text=@@WATEXT@@" aria-label="WhatsApp">
      <i data-lucide="message-circle"></i>WhatsApp
    </a>
  </body>
</html>
"""

POST_BODY = """
    <main id="main">
      <section class="reveal section-pad bg-[#fffaf2]">
        <div class="wrap">
          <nav class="eyebrow" aria-label="Breadcrumb">
            <a href="/">Home</a> &middot; <a href="/@@DIR@@/">@@HUBH1@@</a>
          </nav>
          <div class="max-w-3xl">
            <p class="eyebrow mt-4">@@EYEBROW@@</p>
            <h1 class="font-display mt-3 text-3xl font-black leading-tight md:text-5xl">@@H1@@</h1>
            <p class="lead mt-5">@@LEAD@@</p>
            <p class="fine mt-4">By Renata &middot; @@DATEHUMAN@@ &middot; @@READTIME@@ min</p>
          </div>
        </div>
      </section>
@@SECTIONS@@
      <section class="reveal section-pad bg-white">
        <div class="wrap">
          <div class="max-w-3xl">
            <h2 class="font-display text-2xl font-bold md:text-3xl">@@FAQHEAD@@</h2>
          </div>
          <div class="faq-grid mt-8 grid gap-4 md:grid-cols-2">@@FAQ@@
          </div>
        </div>
      </section>

      <section class="reveal section-pad bg-[#26322f] text-white">
        <div class="wrap">
          <div class="max-w-3xl">
            <h2 class="font-display text-2xl font-bold md:text-3xl">@@CTATITLE@@</h2>
            <p class="prose mt-4 text-white/85">@@CTABODY@@</p>
            <div class="btn-row mt-7">
              <a class="btn btn-whatsapp" href="https://wa.me/@@WA@@?text=@@WATEXT@@"><i data-lucide="message-circle" class="h-4 w-4"></i>@@CTALABEL@@</a>
              <a class="btn btn-on-inverse" href="@@CTAHREF2@@">@@CTALABEL2@@</a>
            </div>
          </div>
        </div>
      </section>

      <section class="reveal section-pad bg-[#f2eadb]">
        <div class="wrap">
          <div class="max-w-3xl">
            <h2 class="font-display text-2xl font-bold md:text-3xl">@@RELHEAD@@</h2>
          </div>
          <div class="mt-8 grid gap-4 md:grid-cols-2">@@RELATED@@
          </div>
        </div>
      </section>
    </main>
"""

MONTHS_EN = ["", "January", "February", "March", "April", "May", "June",
             "July", "August", "September", "October", "November", "December"]
MONTHS_PT = ["", "janeiro", "fevereiro", "marco", "abril", "maio", "junho",
             "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]

COPY = {
    "learn": {"faqhead": "Common questions", "relhead": "Keep going",
              "ctalabel2": "Lessons and prices", "ctahref2": "/pricing.html"},
    "china": {"faqhead": "Perguntas frequentes", "relhead": "Continue lendo",
              "ctalabel2": "Como a traducao automatica falha", "ctahref2": "/traducao-chines-portugues.html"},
    "sourcing": {"faqhead": "Common questions", "relhead": "Related guides",
                 "ctalabel2": "How interpreting works", "ctahref2": "/interpreting.html"},
}

# Poort tegen de fout die hierboven twee keer voorkwam: een nieuwe silo toevoegen en vergeten hem
# in COPY, LANG_RULE of SYSTEMS te zetten. Dat crasht anders pas halverwege een cron-run.
for _s in SILOS:
    for _tabel, _naam in ((COPY, "COPY"), (LANG_RULE, "LANG_RULE"), (SYSTEMS, "SYSTEMS")):
        assert _s in _tabel, "silo '" + _s + "' ontbreekt in " + _naam


def render_post(art, pubdate, manifest):
    silo = art["silo"]
    cfg = SILOS[silo]
    slug = art["slug"]
    canonical = BASE_URL + "/" + cfg["dir"] + "/" + slug + "/"
    h1_esc = esc(art["h1"])
    hl = (art.get("h1_highlight") or "").strip()
    if hl and esc(hl) in h1_esc:
        h1_esc = h1_esc.replace(esc(hl), "<span class=\"accent\">" + esc(hl) + "</span>", 1)
    d = _dt.date.fromisoformat(pubdate)
    if silo == "china":
        datehuman = str(d.day) + " de " + MONTHS_PT[d.month] + " de " + str(d.year)
    else:
        datehuman = MONTHS_EN[d.month] + " " + str(d.day) + ", " + str(d.year)
    readtime = max(2, round(art["_words"] / 200))
    c = COPY[silo]
    html = HEAD_TMPL + POST_BODY + FOOT_TMPL
    repl = {
        "@@LANG@@": cfg["lang"], "@@OGLOCALE@@": cfg["og_locale"], "@@GA4@@": GA4, "@@WA@@": WA,
        "@@WATEXT@@": cfg["wa_text"], "@@DIR@@": cfg["dir"], "@@HUBH1@@": esc(cfg["hub_h1"]),
        "@@TITLE@@": esc(art["title"]), "@@DESC@@": esc(art["meta_description"]),
        "@@OGTITLE@@": esc(art["h1"]), "@@CANONICAL@@": canonical,
        "@@EYEBROW@@": esc(art.get("eyebrow") or cfg["eyebrow_default"]),
        "@@H1@@": h1_esc, "@@LEAD@@": _bold_first_sentence(art["lead"]),
        "@@JSONLD@@": _graph_json(art, canonical, pubdate, silo),
        "@@SECTIONS@@": _sections_html(art["sections"]),
        "@@FAQ@@": _faq_visible(art["faqs"]),
        "@@FAQHEAD@@": c["faqhead"], "@@RELHEAD@@": c["relhead"],
        "@@RELATED@@": _related_html(silo, manifest, slug),
        "@@CTATITLE@@": esc(cfg["cta_title"]), "@@CTABODY@@": esc(cfg["cta_body"]),
        "@@CTALABEL@@": esc(cfg["hub_cta_label"]),
        "@@CTALABEL2@@": esc(c["ctalabel2"]), "@@CTAHREF2@@": c["ctahref2"],
        "@@DATEHUMAN@@": datehuman, "@@READTIME@@": str(readtime),
    }
    for k, v in repl.items():
        html = html.replace(k, v)
    assert "@@" not in html, "onvervangen token in post-template"
    return html


# ---------------------------------------------------------------- hub
HUB_BODY = """
    <main id="main">
      <section class="reveal section-pad bg-[#fffaf2]">
        <div class="wrap">
          <div class="max-w-3xl">
            <p class="eyebrow">@@EYEBROW@@</p>
            <h1 class="font-display mt-3 text-3xl font-black leading-tight md:text-5xl">@@H1@@</h1>
            <p class="lead mt-5">@@INTRO@@</p>
            <div class="btn-row mt-7">
              <a class="btn btn-whatsapp" href="https://wa.me/@@WA@@?text=@@WATEXT@@"><i data-lucide="message-circle" class="h-4 w-4"></i>WhatsApp</a>
              <a class="btn btn-primary" href="@@CTAHREF@@">@@CTALABEL@@</a>
            </div>
          </div>
        </div>
      </section>

      <section class="reveal section-pad bg-white">
        <div class="wrap">
          <div class="grid gap-4 md:grid-cols-2">@@CARDS@@
          </div>
          @@EMPTY@@
        </div>
      </section>
    </main>
"""


def render_hub(silo, manifest):
    cfg = SILOS[silo]
    canonical = BASE_URL + "/" + cfg["dir"] + "/"
    pages = list(reversed(manifest["pages"]))
    cards = []
    for p in pages:
        cards.append(
            "\n            <a class=\"path-card p-7\" href=\"/" + cfg["dir"] + "/" + esc(p["slug"]) + "/\">\n"
            "              <p class=\"eyebrow\">" + esc(p.get("eyebrow", cfg["eyebrow_default"])) + "</p>\n"
            "              <p class=\"font-display mt-2 text-xl font-bold\">" + esc(p["title"]) + "</p>\n"
            "              <p class=\"prose mt-3\">" + esc(p.get("desc", "")) + "</p>\n"
            "            </a>")
    empty = ""
    if not cards:
        empty = "<p class=\"prose\">The first guides are on their way.</p>"
    itemlist = {
        "@context": "https://schema.org",
        "@graph": [
            {"@type": "CollectionPage", "@id": canonical + "#hub", "name": cfg["hub_h1"],
             "description": cfg["hub_desc"], "inLanguage": cfg["lang"], "url": canonical,
             "isPartOf": {"@id": BASE_URL + "/#website"},
             "about": {"@id": BASE_URL + "/about.html#renata"}},
            {"@type": "ItemList", "@id": canonical + "#list",
             "itemListElement": [
                 {"@type": "ListItem", "position": i + 1, "name": p["title"],
                  "url": canonical + p["slug"] + "/"}
                 for i, p in enumerate(pages)]},
        ],
    }
    html = HEAD_TMPL + HUB_BODY + FOOT_TMPL
    repl = {
        "@@LANG@@": cfg["lang"], "@@OGLOCALE@@": cfg["og_locale"], "@@GA4@@": GA4, "@@WA@@": WA,
        "@@WATEXT@@": cfg["wa_text"], "@@TITLE@@": esc(cfg["hub_title"]),
        "@@DESC@@": esc(cfg["hub_desc"]), "@@OGTITLE@@": esc(cfg["hub_h1"]),
        "@@CANONICAL@@": canonical, "@@JSONLD@@": json.dumps(itemlist, ensure_ascii=False, indent=2),
        "@@EYEBROW@@": esc(cfg["eyebrow_default"]), "@@H1@@": esc(cfg["hub_h1"]),
        "@@INTRO@@": esc(cfg["hub_intro"]), "@@CTAHREF@@": cfg["hub_cta_href"],
        "@@CTALABEL@@": esc(cfg["hub_cta_label"]), "@@CARDS@@": "".join(cards), "@@EMPTY@@": empty,
    }
    for k, v in repl.items():
        html = html.replace(k, v)
    assert "@@" not in html, "onvervangen token in hub-template"
    return html


# ---------------------------------------------------------------- publiceren
def update_sitemap(silo, slug):
    sm = SITE_DIR / "sitemap.xml"
    text = sm.read_text(encoding="utf-8")
    today = _dt.date.today().isoformat()
    cfg = SILOS[silo]
    additions = []
    hub_loc = BASE_URL + "/" + cfg["dir"] + "/"
    if hub_loc + "</loc>" not in text:
        additions.append("  <url><loc>" + hub_loc + "</loc><lastmod>" + today
                         + "</lastmod><priority>0.8</priority></url>")
    page_loc = hub_loc + slug + "/"
    if page_loc + "</loc>" not in text:
        additions.append("  <url><loc>" + page_loc + "</loc><lastmod>" + today
                         + "</lastmod><priority>0.6</priority></url>")
    if additions:
        text = text.replace("</urlset>", "\n".join(additions) + "\n</urlset>")
        sm.write_text(text, encoding="utf-8")
    return bool(additions)


def gsc_ping():
    try:
        r = subprocess.run([sys.executable, str(SCRIPTS_DIR / "gsc_sitemap.py"), DOMAIN, "--submit"],
                           cwd=str(SCRIPTS_DIR), capture_output=True, text=True, timeout=120)
        return r.returncode == 0, (r.stdout or r.stderr).strip()
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def write_files(art, pubdate, manifest):
    silo = art["silo"]
    cfg = SILOS[silo]
    slug = art["slug"]
    entry = {"slug": slug, "title": art["title"], "desc": art["meta_description"],
             "eyebrow": art.get("eyebrow") or cfg["eyebrow_default"],
             "target_query": art["target_query"], "date": pubdate, "source": art["source"],
             "text": art["_body_text"],
             # Volledige artikel-JSON meebewaren, zodat een wijziging aan het template of aan de
             # navigatie ALLE bestaande paginas kan herbouwen zonder DeepSeek opnieuw te betalen.
             "article": {k: v for k, v in art.items() if not k.startswith("_")}}
    manifest["pages"] = [p for p in manifest["pages"] if p["slug"] != slug] + [entry]
    page_dir = SITE_DIR / cfg["dir"] / slug
    page_dir.mkdir(parents=True, exist_ok=True)
    (page_dir / "index.html").write_text(render_post(art, pubdate, manifest), encoding="utf-8")
    (SITE_DIR / cfg["dir"] / "index.html").write_text(render_hub(silo, manifest), encoding="utf-8")
    return page_dir / "index.html"


def git_publish(paths, message):
    """Commit + push naar main. Netlify bouwt zelf vanaf main, dus push IS de deploy."""
    def run(*args):
        return subprocess.run(["git"] + list(args), cwd=str(SITE_DIR),
                              capture_output=True, text=True, timeout=180)
    run("config", "user.name", "renata-lp-bot")
    run("config", "user.email", "bot@portuguesewithrenata.com")
    for p in paths:
        run("add", str(p))
    st = run("status", "--porcelain")
    if not (st.stdout or "").strip():
        return False, "niets te committen"
    c = run("commit", "-m", message)
    if c.returncode != 0:
        return False, (c.stdout + c.stderr).strip()
    p = run("push", "origin", "HEAD:main")
    if p.returncode != 0:
        return False, (p.stdout + p.stderr).strip()
    return True, "gepusht naar main"


# ---------------------------------------------------------------- commands
def _gsc():
    token, terr = gsc_token()
    if not token or terr:
        return None, None
    site, _ = resolve_site(token, DOMAIN)
    return token, site


def cmd_topics():
    token, site = _gsc()
    for silo in SILOS:
        manifest = load_manifest(silo)
        queue = build_queue(token, site, silo, manifest)
        print("\n=== " + silo.upper() + " (" + str(len(queue)) + " open, "
              + str(len(manifest["pages"])) + " gepubliceerd) ===")
        for i, t in enumerate(queue[:15], 1):
            src = ("GSC " + str(int(t["impressions"])) + "imp/pos"
                   + ("%.1f" % t["position"])) if t["source"] == "gsc" else "seed"
            print("  " + str(i).rjust(2) + ". [" + src + "] " + t["q"])
    return 0


def _next_silo():
    """Toerbeurt op basis van wie het minst heeft, zodat beide silos groeien."""
    counts = {s: len(load_manifest(s)["pages"]) for s in SILOS}
    # china eerst bij gelijkstand: dat is de silo met de commerciele dienst erachter
    return min(sorted(SILOS, key=lambda s: 0 if s == "china" else 1), key=lambda s: counts[s])


def publish_one(silo, token, site, dry_run=False, forced=None):
    manifest = load_manifest(silo)
    if forced:
        queue = [{"q": forced, "angle": "", "source": "manual", "silo": silo,
                  "impressions": 0, "position": 0}]
    else:
        queue = build_queue(token, site, silo, manifest)
    if not queue:
        print("[" + silo + "] geen open onderwerpen. Backlog leeg = motor doet niets (ontwerp, geen storing).")
        return None

    for topic in queue[:5]:
        print("\n>> [" + silo + "] genereren: " + topic["q"] + "  [" + topic["source"] + "]")
        try:
            art = repair(generate(topic))
        except Exception as e:  # noqa: BLE001
            print("   generatie faalde: " + str(e))
            continue
        ok, reasons = gate(art, manifest)
        if not ok:
            print("   POORT AFGEWEZEN: " + "; ".join(reasons) + "  -> volgend onderwerp")
            continue
        pubdate = _dt.date.today().isoformat()
        path = write_files(art, pubdate, manifest)
        print("   POORT OK (" + str(art["_words"]) + " woorden) -> " + str(path))
        if dry_run:
            (path.parent / "_pending.json").write_text(json.dumps(art, ensure_ascii=False),
                                                       encoding="utf-8")
            print("   DRY-RUN: lokaal geschreven, manifest NIET vastgelegd, niets gepusht.")
            print("   Titel: " + art["title"])
            print("   Meta:  " + art["meta_description"])
            print("   URL:   " + BASE_URL + "/" + SILOS[silo]["dir"] + "/" + art["slug"] + "/")
            return art
        update_sitemap(silo, art["slug"])
        save_manifest(silo, manifest)
        return art
    print("[" + silo + "] geen enkel onderwerp haalde de poort. Niets geschreven (correct).")
    return None


def cmd_run(count=1, dry_run=False, silo=None, forced=None):
    token, site = _gsc()
    if forced and not silo:
        print("--topic vereist ook --silo learn|china")
        return 1
    order = [silo] if silo else []
    if not order:
        first = _next_silo()
        order = [first] + [s for s in SILOS if s != first]
    published = []
    for i in range(max(1, count)):
        target = order[i % len(order)]
        art = publish_one(target, token, site, dry_run=dry_run, forced=forced if i == 0 else None)
        if art:
            published.append(art)
    if not published or dry_run:
        return 0

    # In GitHub Actions doet de workflow zelf commit+rebase+push (les uit de acinstall-incidenten:
    # rebasen op main voor de push, anders botst de bot met handmatig werk). Lokaal pusht de motor wel.
    if os.environ.get("LP_SKIP_GIT") == "1":
        for a in published:
            print("GESCHREVEN: /" + SILOS[a["silo"]]["dir"] + "/" + a["slug"] + "/")
        return 0

    paths = [str(SITE_DIR / "sitemap.xml")]
    for a in published:
        paths.append(str(SITE_DIR / SILOS[a["silo"]]["dir"]))
    titles = ", ".join(a["slug"] for a in published)
    ok, msg = git_publish(paths, "lp: auto-publish " + _dt.date.today().isoformat() + " (" + titles + ")")
    print("\nGit: " + msg)
    if not ok:
        return 1
    ok_ping, pmsg = gsc_ping()
    last = pmsg.splitlines()[-1] if pmsg else ""
    print("GSC-sitemap: " + ("ok" if ok_ping else "mislukt") + " (" + last + ")")
    for a in published:
        print("LIVE over ~2 min: " + BASE_URL + "/" + SILOS[a["silo"]]["dir"] + "/" + a["slug"] + "/")
    return 0


def cmd_hubs():
    for silo in SILOS:
        m = load_manifest(silo)
        d = SITE_DIR / SILOS[silo]["dir"]
        d.mkdir(parents=True, exist_ok=True)
        (d / "index.html").write_text(render_hub(silo, m), encoding="utf-8")
        print("Hub /" + SILOS[silo]["dir"] + "/ herbouwd met " + str(len(m["pages"])) + " paginas.")
    return 0


def cmd_rerender():
    """Bouw alle bestaande paginas opnieuw uit het manifest, zonder DeepSeek.
    Gebruik dit na elke wijziging aan het template, de navigatie of de footer."""
    total = 0
    for silo in SILOS:
        m = load_manifest(silo)
        cfg = SILOS[silo]
        done = 0
        for p in m["pages"]:
            art = p.get("article")
            if not art:
                print("  overgeslagen (geen article in manifest, van voor deze versie): " + p["slug"])
                continue
            art["_body_text"] = p.get("text", "")
            art["_words"] = len(art["_body_text"].split())
            page_dir = SITE_DIR / cfg["dir"] / p["slug"]
            page_dir.mkdir(parents=True, exist_ok=True)
            (page_dir / "index.html").write_text(
                render_post(art, p["date"], m), encoding="utf-8")
            done += 1
        d = SITE_DIR / cfg["dir"]
        if m["pages"]:
            d.mkdir(parents=True, exist_ok=True)
            (d / "index.html").write_text(render_hub(silo, m), encoding="utf-8")
        print("/" + cfg["dir"] + "/: " + str(done) + " paginas herbouwd")
        total += done
    print("Totaal " + str(total) + " paginas herbouwd uit het manifest.")
    return 0


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("command", choices=["topics", "run", "hubs", "rerender"])
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--silo", choices=list(SILOS))
    ap.add_argument("--topic")
    a = ap.parse_args()
    if a.command == "topics":
        return cmd_topics()
    if a.command == "hubs":
        return cmd_hubs()
    if a.command == "rerender":
        return cmd_rerender()
    return cmd_run(count=a.count, dry_run=a.dry_run, silo=a.silo, forced=a.topic)


if __name__ == "__main__":
    sys.exit(main())
