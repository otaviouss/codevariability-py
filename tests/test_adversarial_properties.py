"""Independent bounded tests against the installed wheel, seed 20261003."""
import concurrent.futures
import hashlib
import json
import math
import random
import re
import tempfile
import unicodedata
from collections import Counter
from functools import lru_cache
from pathlib import Path

import pandas as pd
import pytest
from hypothesis import given, seed, settings, strategies as st
from codevariability import AnalysisError, AnalysisResult, analyze, compare_groups, load_matrix_json
from codevariability.ast_tree_edit import NormalizedAstNode, tree_edit_distance, tree_edit_similarity
from codevariability.metrics import cosine, jaccard, lcs, levenshtein

AUDIT_SEED = 20261003

def words(source):
    return re.findall(r"\b\w+\b", " ".join(unicodedata.normalize("NFC", source).casefold().split()))

@seed(AUDIT_SEED)
@settings(max_examples=300, deadline=None, database=None)
@given(st.text(max_size=128), st.text(max_size=128))
def test_text_metrics_against_independent_counts(left, right):
    a, b = Counter(words(left)), Counter(words(right))
    denominator = math.sqrt(sum(v*v for v in a.values()) * sum(v*v for v in b.values()))
    reference_cosine = sum(v*b[k] for k,v in a.items()) / denominator if denominator else 0
    union = set(a) | set(b)
    reference_jaccard = len(set(a) & set(b)) / len(union) if union else 1
    for metric, reference in [(cosine, reference_cosine), (jaccard, reference_jaccard)]:
        matrix = metric({"left.txt":left, "right.txt":right})
        assert matrix.iat[0,1] == pytest.approx(reference, abs=1e-12)
        assert matrix.iat[0,1] == matrix.iat[1,0]
        assert 0 <= matrix.iat[0,1] <= 1+1e-12

def sequence_reference(a,b):
    lcs_table = [[0]*(len(b)+1) for _ in range(len(a)+1)]
    edit = [[0]*(len(b)+1) for _ in range(len(a)+1)]
    for i in range(len(a)+1): edit[i][0]=i
    for j in range(len(b)+1): edit[0][j]=j
    for i in range(1,len(a)+1):
        for j in range(1,len(b)+1):
            lcs_table[i][j] = lcs_table[i-1][j-1]+1 if a[i-1]==b[j-1] else max(lcs_table[i-1][j],lcs_table[i][j-1])
            edit[i][j] = min(edit[i-1][j]+1,edit[i][j-1]+1,edit[i-1][j-1]+(a[i-1]!=b[j-1]))
    n=max(len(a),len(b))
    return (lcs_table[-1][-1]/n,1-edit[-1][-1]/n) if n else (1,1)

@seed(AUDIT_SEED)
@settings(max_examples=200, deadline=None, database=None)
@given(st.lists(st.sampled_from(["a","b","c","+","-","1","2"]),max_size=12),
       st.lists(st.sampled_from(["a","b","c","+","-","1","2"]),max_size=12))
def test_token_metrics_against_independent_dynamic_programming(a,b):
    reference_lcs,reference_edit=sequence_reference(a,b)
    sources={"a.py":" ".join(a),"b.py":" ".join(b)}
    assert lcs(sources).iat[0,1] == pytest.approx(reference_lcs)
    assert levenshtein(sources).iat[0,1] == pytest.approx(reference_edit)

@lru_cache(maxsize=None)
def size(tree):
    return 0 if tree is None else 1+sum(size(child) for child in tree.children)

@lru_cache(maxsize=None)
def independent_forest_distance(left,right):
    if not left:return sum(size(t) for t in right)
    if not right:return sum(size(t) for t in left)
    a,b=left[0],right[0]
    return min(1+independent_forest_distance(a.children+left[1:],right),
               1+independent_forest_distance(left,b.children+right[1:]),
               (a.label!=b.label)+independent_forest_distance(a.children,b.children)
                 +independent_forest_distance(left[1:],right[1:]))

def encode(tree):
    return None if tree is None else {"label":tree.label,"children":[encode(c) for c in tree.children]}

def generate_tree_cases():
    rng=random.Random(AUDIT_SEED)
    def node(depth=0):
        return NormalizedAstNode(rng.choice("abc"),tuple(node(depth+1) for _ in range(rng.randrange(3) if depth<2 else 0)))
    trees=[None,NormalizedAstNode("a"),NormalizedAstNode("b")]+[node() for _ in range(25)]
    cases=[]
    for _ in range(200):
        a,b=rng.choice(trees),rng.choice(trees)
        distance=independent_forest_distance(() if a is None else (a,),() if b is None else (b,))
        similarity=1-distance/(size(a)+size(b)-1) if a is not None and b is not None else 1 if a is b is None else 0
        cases.append((a,b,distance,similarity))
    return cases

def test_ted_against_independent_ordered_forest_oracle(tmp_path):
    cases=generate_tree_cases()
    for a,b,distance,similarity in cases:
        assert tree_edit_distance(a,b)==distance
        assert tree_edit_distance(b,a)==distance
        assert tree_edit_similarity(a,b)==pytest.approx(similarity)
    target=tmp_path/"tree-oracle-cases.json"
    target.write_text(json.dumps([{"left":encode(a),"right":encode(b),"distance":d,"similarity":s} for a,b,d,s in cases]))

@pytest.mark.parametrize("value",[None,True,False,{},[],"x",[1],float("nan"),float("inf")])
def test_invalid_matrix_payloads_raise_analysis_error(tmp_path,value):
    payload={"schema_version":"codevariability.matrix.v1","metric":"custom","files":["a","b"],"matrix":[[1,value],[value,1]]}
    p=tmp_path/"invalid.json";p.write_text(json.dumps(payload))
    with pytest.raises(AnalysisError):load_matrix_json(p)

@seed(AUDIT_SEED)
@settings(max_examples=250,deadline=None,database=None)
@given(st.binary(max_size=128))
def test_json_byte_fuzz_has_controlled_errors(raw):
    with tempfile.TemporaryDirectory() as directory:
        p=Path(directory)/"matrix.json";p.write_bytes(raw)
        try:load_matrix_json(p)
        except AnalysisError:pass
        else:pytest.fail("Random payload unexpectedly satisfied the strict matrix schema")

def fixture_groups(root):
    folders=[]
    for group,sources in [("left",["x=1","x=1"]),("right",["y=2","y=3"]),("third",["z=4","z=5"])]:
        folder=root/group;folder.mkdir()
        for index,source in enumerate(sources):(folder/f"{index}.py").write_text(source)
        folders.append(folder)
    return folders

def test_group_name_cannot_silently_overwrite_homogeneity(tmp_path):
    left,right,_=fixture_groups(tmp_path)
    result=compare_groups(left,right,metrics="jaccard",group_names=("difference","right"),permutations=9)
    assert result.overview.loc["Textual", result.within_group_columns[0]]==1.0
    assert result.summary.loc["jaccard", "within_difference"]==pytest.approx(2/3)

def test_permutations_are_repeatable_and_do_not_touch_global_rng(tmp_path):
    groups=fixture_groups(tmp_path);state=random.getstate()
    results=[compare_groups(dict(zip(["a","b","c"],groups)),metrics="jaccard",permutations=49,random_state=AUDIT_SEED) for _ in range(3)]
    assert state==random.getstate()
    for result in results[1:]:
        pd.testing.assert_frame_equal(result.global_test,results[0].global_test)
        pd.testing.assert_frame_equal(result.pairwise,results[0].pairwise)

def test_external_matrix_is_copied_and_outputs_are_atomic(tmp_path):
    frame=pd.DataFrame([[1,.5],[.5,1]],index=["a","b"],columns=["a","b"])
    frame.attrs={"adapter_metadata":{"value":1}}
    result=AnalysisResult({"metric":frame},["a","b"])
    frame.iat[0,1]=0
    assert result.matrices["metric"].iat[0,1]==.5
    output=tmp_path/"outputs"
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _:result.export(output,formats="json"),range(12)))
    assert json.loads((output/"analysis.json").read_text())["files"]==["a","b"]
    assert not list(output.glob(".codevariability-*"))

def test_utf8_input_rejected_and_source_never_executed(tmp_path):
    p=tmp_path/"invalid.py";p.write_bytes(b'x="\xff"')
    with pytest.raises(AnalysisError):analyze(p)
    marker=tmp_path/"execution-marker"
    p.write_text(f'from pathlib import Path\nPath({str(marker)!r}).write_text("unexpected")')
    before=hashlib.sha256(p.read_bytes()).hexdigest()
    for _ in range(3):analyze(p)
    assert not marker.exists()
    assert hashlib.sha256(p.read_bytes()).hexdigest()==before

def test_json_numerical_limits_and_duplicate_keys(tmp_path):
    for text in ['{"schema_version":"x","schema_version":"y"}', '['*1200+']'*1200,
                 '{"schema_version":"codevariability.matrix.v1","metric":"m","files":["a"],"matrix":[[1e999]]}']:
        p=tmp_path/"bad.json";p.write_text(text)
        with pytest.raises(AnalysisError):load_matrix_json(p)
