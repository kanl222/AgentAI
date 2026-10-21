"""Lexical features shared by ranking and question interpretation."""
import re


QUESTION_WORDS = frozenset('что такое как какие какой какая где когда почему зачем '
                           'расскажи расскажите покажи покажите объясни объясните '
                           'пожалуйста'.split())


def tokenize(text):
    return re.findall(r'\w+',text.lower()) or ['_']


def heading_in_question(text, query):
    """Match an ordered heading inside a naturally phrased question."""
    question = ' '+' '.join(tokenize(query)).replace('ё', 'е')+' '
    # PDF exports often place numbered bullets before the actual heading.
    lines = [line for line in text.splitlines()[:80] if re.search(r'[а-яА-ЯёЁa-zA-Z]', line)][:8]
    return any(3 <= len(words) <= 16 and
               ' '+' '.join(words).replace('ё', 'е')+' ' in question
               for words in (tokenize(line) for line in lines))


def lexical_tokens(text):
    """Keep identifiers and tolerate Russian spelling and split abbreviations.

    These are lexical features only: source text and dense embeddings stay intact.
    Join short uppercase groups on one line, e.g. АБ «ВГ» -> абвг, without a
    domain dictionary or joining unrelated table rows across line breaks.
    """
    words = [word for word in tokenize(text.replace('ё', 'е').replace('Ё', 'Е'))
             if word not in QUESTION_WORDS] or ['_']
    abbreviations = [re.sub(r'[^А-ЯЁ]', '', match.group()).lower().replace('ё', 'е')
                     for match in re.finditer(
                         r'(?<!\w)[А-ЯЁ]{2,4}[ \t]+[«"„“]?[А-ЯЁ]{2,4}(?!\w)', text)]
    return words + abbreviations + ['ru:'+word[:6] for word in words if len(word)>=7 and re.fullmatch('[а-яё]+',word)]
