import hashlib

PDF = b"%PDF-1.4 test coa content"


def upload(c, name="coa.pdf", data=PDF, **form):
    return c.post("/api/documents", files={"file": (name, data)}, data={"title": "COA Batch 1", "doc_type": "COA", **form})


def test_upload_download_roundtrip_with_integrity_hash(admin):
    r = upload(admin, entity_type="supplier", entity_id="7")
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["versions"][0]["sha256"] == hashlib.sha256(PDF).hexdigest() and d["links"][0]["entity_type"] == "supplier"
    dl = admin.get(f"/api/documents/{d['id']}/download")
    assert dl.content == PDF and "attachment" in dl.headers["content-disposition"] and dl.headers["x-content-type-options"] == "nosniff"
    assert admin.get("/api/admin/audit-logs", params={"entity": "document", "action": "download"}).json()["total"] == 1


def test_new_version_keeps_history(admin):
    d = upload(admin).json()
    r = admin.post(f"/api/documents/{d['id']}/versions", files={"file": ("coa-v2.pdf", b"%PDF second")}, data={"note": "corrected"})
    assert r.status_code == 201 and r.json()["current_version_no"] == 2 and len(r.json()["versions"]) == 2
    assert admin.get(f"/api/documents/{d['id']}/download").content == b"%PDF second"
    assert admin.get(f"/api/documents/{d['id']}/download", params={"version": 1}).content == PDF
    assert admin.get(f"/api/documents/{d['id']}/download", params={"version": 9}).status_code == 404


def test_rejects_dangerous_empty_and_oversized_files(admin, monkeypatch):
    for name in ("run.exe", "shell.sh", "page.html", "x.svg", "noext", "archive.zip"):
        assert upload(admin, name=name).status_code == 422, name
    assert upload(admin, data=b"").status_code == 422
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "max_upload_mb", 0)
    assert upload(admin).status_code == 422
    assert upload(admin).status_code in (422,)  # still rejected, nothing stored


def test_unknown_doc_type_and_half_specified_link_rejected(admin):
    assert upload(admin, doc_type="NOPE").status_code == 422
    assert upload(admin, entity_type="supplier").status_code == 422


def test_filename_is_sanitised(admin):
    d = upload(admin, name="../../etc/pass wd?.pdf").json()
    assert "/" not in d["versions"][0]["filename"] and ".." not in d["versions"][0]["filename"].replace("..pdf", "")


def test_filter_by_linked_record_and_soft_delete(admin):
    a = upload(admin, entity_type="supplier", entity_id="1").json()
    upload(admin, entity_type="supplier", entity_id="2")
    assert [x["id"] for x in admin.get("/api/documents", params={"entity_type": "supplier", "entity_id": "1"}).json()["items"]] == [a["id"]]
    assert admin.post(f"/api/documents/{a['id']}/links", json={"entity_type": "item", "entity_id": "5"}).status_code == 201
    assert admin.post(f"/api/documents/{a['id']}/links", json={"entity_type": "item", "entity_id": "5"}).status_code == 201  # idempotent
    assert len(admin.get(f"/api/documents/{a['id']}").json()["links"]) == 2
    assert admin.delete(f"/api/documents/{a['id']}").status_code == 204
    assert admin.get(f"/api/documents/{a['id']}").status_code == 404
    assert admin.get(f"/api/documents/{a['id']}/download").status_code == 404


def test_document_permissions(login, admin):
    d = upload(admin).json()
    op = login("production_operator")  # no document permissions at all
    assert op.get("/api/documents").status_code == 403
    assert op.get(f"/api/documents/{d['id']}/download").status_code == 403
    sales = login("sales_operator")  # may read metadata but not download or upload
    assert sales.get("/api/documents").status_code == 200
    assert sales.get(f"/api/documents/{d['id']}/download").status_code == 403
    assert upload(sales).status_code == 403
