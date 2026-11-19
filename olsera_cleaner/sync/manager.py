from olsera_cleaner.api.adapter import parse_page, ResponseFormatError

class SyncManager:
    def __init__(self, client, db, settings):
        self.client=client
        self.db=db
        self.settings=settings

    def sync(self, resource="products", progress=None):
        if resource not in ("products", "collections", "product_groups"):
            raise ValueError("Jenis sinkronisasi tidak dikenal")
        run=self.db.start_run(resource)
        method_name={"products":"product_list", "collections":"collections", "product_groups":"product_groups"}[resource]
        fetch=getattr(self.client,method_name)
        try:
            page=1
            first_total=None
            last_page=None
            total_pages=0
            while True:
                payload=fetch(page=page,per_page=self.settings.page_size)
                parsed=parse_page(payload,page)
                if first_total is None:
                    first_total=parsed.total
                    last_page=parsed.last_page
                if parsed.total != first_total or parsed.last_page != last_page:
                    raise ResponseFormatError("Pagination berubah selama sinkronisasi; ulangi")
                self.db.stage_page(run,page,parsed.items,resource)
                total_pages+=1
                if progress:
                    progress(page,last_page,len(parsed.items))
                if page >= last_page:
                    break
                page+=1
                if page > 100000:
                    raise ResponseFormatError("Batas pengaman pagination tercapai")
            self.db.finish_run(run,first_total,total_pages,resource,self.settings.expected_store_id)
            return {"run_id":run,"resource":resource,"total":first_total,"pages":total_pages}
        except Exception as exc:
            self.db.fail_run(run,exc)
            raise
