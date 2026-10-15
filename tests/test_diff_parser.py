from pr_review_agent.diff_parser import parse_diff, split_diff

MODIFIED = """\
diff --git a/src/app.py b/src/app.py
index 1111111..2222222 100644
--- a/src/app.py
+++ b/src/app.py
@@ -1,4 +1,5 @@
 import os
-x = 1
+x = 2
+y = 3
 z = 4
 w = 5
@@ -20,3 +21,3 @@ def f():
     a = 1
-    b = 2
+    b = 3
     c = 4
"""


def test_modified_file_multiple_hunks() -> None:
    fd = parse_diff(MODIFIED)["src/app.py"]
    assert fd.status == "modified"
    assert fd.old_path == "src/app.py"
    assert fd.added_lines == {2, 3, 22}
    assert fd.commentable_lines == {1, 2, 3, 4, 5, 21, 22, 23}
    assert not fd.is_binary
    assert len(fd.hunks) == 2


def test_new_file() -> None:
    diff = """\
diff --git a/new.py b/new.py
new file mode 100644
index 0000000..3333333
--- /dev/null
+++ b/new.py
@@ -0,0 +1,2 @@
+print('a')
+print('b')
"""
    fd = parse_diff(diff)["new.py"]
    assert fd.status == "added"
    assert fd.old_path is None
    assert fd.added_lines == fd.commentable_lines == {1, 2}


def test_deleted_file() -> None:
    diff = """\
diff --git a/old.py b/old.py
deleted file mode 100644
index 3333333..0000000
--- a/old.py
+++ /dev/null
@@ -1,2 +0,0 @@
-print('a')
-print('b')
"""
    fd = parse_diff(diff)["old.py"]
    assert fd.status == "deleted"
    assert fd.path == "old.py"
    assert fd.added_lines == set() and fd.commentable_lines == set()


def test_renamed_with_changes() -> None:
    diff = """\
diff --git a/lib/old_name.py b/lib/new_name.py
similarity index 90%
rename from lib/old_name.py
rename to lib/new_name.py
index 1111111..2222222 100644
--- a/lib/old_name.py
+++ b/lib/new_name.py
@@ -1,2 +1,2 @@
 a = 1
-b = 2
+b = 3
"""
    fd = parse_diff(diff)["lib/new_name.py"]
    assert fd.status == "renamed"
    assert fd.old_path == "lib/old_name.py"
    assert fd.added_lines == {2}


def test_pure_rename_without_hunks() -> None:
    diff = """\
diff --git a/a.txt b/b.txt
similarity index 100%
rename from a.txt
rename to b.txt
"""
    fd = parse_diff(diff)["b.txt"]
    assert fd.status == "renamed" and fd.old_path == "a.txt"
    assert fd.commentable_lines == set()


def test_binary_file() -> None:
    diff = """\
diff --git a/logo.png b/logo.png
new file mode 100644
index 0000000..4444444
Binary files /dev/null and b/logo.png differ
"""
    fd = parse_diff(diff)["logo.png"]
    assert fd.is_binary
    assert fd.status == "added"


def test_no_newline_at_end_of_file() -> None:
    diff = """\
diff --git a/f.txt b/f.txt
--- a/f.txt
+++ b/f.txt
@@ -1,2 +1,2 @@
 one
-two
\\ No newline at end of file
+two
\\ No newline at end of file
diff --git a/g.txt b/g.txt
--- a/g.txt
+++ b/g.txt
@@ -1 +1 @@
-x
+y
"""
    files = parse_diff(diff)
    assert files["f.txt"].added_lines == {2}
    assert files["f.txt"].commentable_lines == {1, 2}
    assert files["f.txt"].hunks[0].lines[-1].no_newline_at_eof
    assert files["g.txt"].added_lines == {1}


def test_removed_lines_that_look_like_headers() -> None:
    diff = """\
diff --git a/notes.md b/notes.md
--- a/notes.md
+++ b/notes.md
@@ -1,3 +1,3 @@
--- old heading
+++ new heading
 body
-end
+fin
"""
    fd = parse_diff(diff)["notes.md"]
    assert fd.added_lines == {1, 3}
    assert fd.commentable_lines == {1, 2, 3}


def test_multiple_files_and_split() -> None:
    diff = (
        MODIFIED
        + """\
diff --git a/b.py b/b.py
--- a/b.py
+++ b/b.py
@@ -5,0 +6,1 @@
+new_line()
"""
    )
    files = parse_diff(diff)
    assert set(files) == {"src/app.py", "b.py"}
    assert files["b.py"].added_lines == {6}
    chunks = split_diff(diff)
    assert chunks["b.py"].startswith("diff --git a/b.py b/b.py")
    assert "+new_line()" in chunks["b.py"]
    assert "b.py" not in chunks["src/app.py"]
    assert parse_diff(chunks["src/app.py"])["src/app.py"].added_lines == {2, 3, 22}


def test_plain_unified_diff_without_git_header() -> None:
    diff = """\
--- a/x.py
+++ b/x.py
@@ -1 +1,2 @@
 a
+b
--- a/y.py
+++ b/y.py
@@ -1 +1 @@
-c
+d
"""
    files = parse_diff(diff)
    assert files["x.py"].added_lines == {2}
    assert files["y.py"].added_lines == {1}


def test_path_with_spaces_and_empty_input() -> None:
    diff = """\
diff --git a/my dir/f.py b/my dir/f.py
--- a/my dir/f.py
+++ b/my dir/f.py
@@ -1 +1 @@
-a
+b
"""
    assert "my dir/f.py" in parse_diff(diff)
    assert parse_diff("") == {}
