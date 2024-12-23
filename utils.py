import os
from typing import Dict, Optional, Sequence, Any, List, Union
from ftfy import fix_text
import re
import json
from rerank.rankllm import PromptMode
from abc import ABC, abstractmethod
from enum import Enum, unique
from pyserini.search import JLuceneSearcherResult
from config import PROJECT_DIR

def get_topics_dl22():
    topics = {}
    with open(os.path.join(PROJECT_DIR, 'data/ms_marco/dl22/topics.txt)'), 'r') as f: 
        for line in f:
            qid, text = line.split('\t')
            topics[qid] = text
    return topics

def get_qrels_dl22():
    qrels = {}
    with open(os.path.join(PROJECT_DIR, 'data/ms_marco/dl22/qrels.txt'), 'r') as f:
        qrel_data = f.readlines()
    for line in qrel_data:
        line = line.strip().split()
        query = line[0]
        doc_id = line[2]
        rel = int(line[3])
        if query not in qrels:
            qrels[query] = {}
        qrels[query][doc_id] = rel
    return qrels

# for constructing the input during training and inference
def add_prefix_prompt(promptmode: PromptMode, query: str, num: int) -> str:
    if promptmode == str(PromptMode.RANK_GPT):
        return f"I will provide you with {num} passages, each indicated by a numerical identifier []. Rank the passages based on their relevance to the search query: {query}.\n"
    elif promptmode == str(PromptMode.RANK_GPT_new):
        return f"I will provide you with {num} passages, each indicated by a numerical identifier. Rank the passages based on their relevance to the search query: {query}.\n"
    else: 
        raise ValueError('not supported promptmode')

def add_post_prompt(promptmode: PromptMode, variable_passages: bool, query: str, num: int) -> str:
    if promptmode == str(PromptMode.RANK_GPT):
        example_ordering = "[2] > [1]" if variable_passages else "[4] > [2]"
        return f"Search Query: {query}.\nRank the {num} passages above based on their relevance to the search query. All the passages should be included and listed using identifiers, in descending order of relevance. The output format should be [] > [], e.g., {example_ordering}, Only respond with the ranking results, do not say any word or explain."
    elif promptmode == str(PromptMode.RANK_GPT_new):
        example_ordering = "2>1" if variable_passages else "4>2"
        return f"Search Query: {query}.\nRank the {num} passages above based on their relevance to the search query. All the passages should be included and listed using identifiers, in descending order of relevance, e.g., {example_ordering}. Only respond with the ranking results, do not say any word or explain."
    else: 
        raise ValueError('not supported promptmode')

def convert_doc_to_prompt_content(tokenizer, doc: Dict[str, Any], max_length: int, truncate_by_word = True) -> str:
    if "text" in doc:
        content = doc["text"]
    elif "segment" in doc:
        content = doc["segment"]
    elif "contents" in doc:
        content = doc["contents"]
    elif "body" in doc:
        content = doc["body"]
    else:
        content = doc["passage"]
    if "title" in doc and doc["title"]:
        content = "Title: " + doc["title"] + " " + "Content: " + content
    content = content.strip()
    content = fix_text(content)
    if truncate_by_word:
        content = " ".join(content.split()[: int(max_length)])
    else: 
        content = tokenizer.convert_tokens_to_string(tokenizer.tokenize(content)[:max_length])
    # For Japanese should cut by character: content = content[:int(max_length)]
    return replace_number(content)

def replace_number(s: str) -> str:
    return re.sub(r"\[(\d+)\]", r"(\1)", s)


DOC_FORMAT_DIC = {
    "msmarco-v1-passage": "{contents}",
    "beir-v1.0.0-scifact-flat": "{title}. {text}",
    "beir-v1.0.0-fiqa-flat": "{text}",
    "beir-v1.0.0-nfcorpus-flat": "{title}. {text}",
    "beir-v1.0.0-fever-flat": "{title}. {text}",
    "beir-v1.0.0-climate-fever-flat": "{title}. {text}",
    "beir-v1.0.0-hotpotqa-flat": "{title}. {text}",
    "beir-v1.0.0-nq-flat": "{title}. {text}",
    "beir-v1.0.0-quora-flat": "{text}",
    "beir-v1.0.0-trec-covid-flat": "{title}. {text}",
    "beir-v1.0.0-webis-touche2020-flat": "{title}. {text}",
    "beir-v1.0.0-arguana-flat": "{title}. {text}",
    "beir-v1.0.0-dbpedia-entity-flat": "{title}. {text}",
    "beir-v1.0.0-robust04-flat": "{text}",
    "beir-v1.0.0-scidocs-flat": "{title}. {text}",
    "beir-v1.0.0-trec-news-flat": "{title}. {text}",
    "beir-v1.0.0-signal1m-flat": "{text}",
    # "beir-v1.0.0-hotpotqa-flat": "title: {title} content: {text}",
}


############################## below is pyserini output_writer.py, we added utf-8 encoding ##############################
@unique
class OutputFormat(Enum):
    TREC = 'trec'
    MSMARCO = "msmarco"
    KILT = 'kilt'

class OutputWriter(ABC):
    def __init__(self, file_path: str, mode: str = 'w',
                 max_hits: int = 1000, tag: str = None, topics: dict = None,
                 use_max_passage: bool = False, max_passage_delimiter: str = None, max_passage_hits: int = 100):
        self.file_path = file_path
        self.mode = mode
        self.tag = tag
        self.topics = topics
        self.use_max_passage = use_max_passage
        self.max_passage_delimiter = max_passage_delimiter if use_max_passage else None
        self.max_hits = max_passage_hits if use_max_passage else max_hits
        self._file = None

    def __enter__(self):
        dirname = os.path.dirname(self.file_path)
        if dirname:
            os.makedirs(dirname, exist_ok=True)
        self._file = open(self.file_path, self.mode, encoding='utf-8')
        return self

    def __exit__(self, exc_type, exc_value, exc_traceback):
        self._file.close()

    def hits_iterator(self, hits: List[JLuceneSearcherResult]):
        unique_docs = set()
        rank = 1
        for hit in hits:
            if self.use_max_passage and self.max_passage_delimiter:
                docid = hit.docid.split(self.max_passage_delimiter)[0]
            else:
                docid = hit.docid.strip()

            if self.use_max_passage:
                if docid in unique_docs:
                    continue
                unique_docs.add(docid)

            yield docid, rank, hit.score, hit

            rank = rank + 1
            if rank > self.max_hits:
                break

    @abstractmethod
    def write(self, topic: str, hits: List[JLuceneSearcherResult]):
        raise NotImplementedError()

class TrecWriter(OutputWriter):
    def write(self, topic: str, hits: List[JLuceneSearcherResult]):
        for docid, rank, score, _ in self.hits_iterator(hits):
            self._file.write(f'{topic} Q0 {docid} {rank} {score:.6f} {self.tag}\n')

class MsMarcoWriter(OutputWriter):
    def write(self, topic: str, hits: List[JLuceneSearcherResult]):
        for docid, rank, score, _ in self.hits_iterator(hits):
            self._file.write(f'{topic}\t{docid}\t{rank}\n')

class KiltWriter(OutputWriter):
    def write(self, topic: str, hits: List[JLuceneSearcherResult]):
        datapoint = self.topics[topic]
        provenance = []
        for docid, rank, score, _ in self.hits_iterator(hits):
            provenance.append({"wikipedia_id": docid})
        datapoint["output"] = [{"provenance": provenance}]
        json.dump(datapoint, self._file)
        self._file.write('\n')

def get_output_writer(file_path: str, output_format: OutputFormat, *args, **kwargs) -> OutputWriter:
    mapping = {
        OutputFormat.TREC: TrecWriter,
        OutputFormat.MSMARCO: MsMarcoWriter,
        OutputFormat.KILT: KiltWriter,
    }
    return mapping[output_format](file_path, *args, **kwargs)

def tie_breaker(hits):
    return sorted(hits, key=lambda x: (-x.score, x.docid))

