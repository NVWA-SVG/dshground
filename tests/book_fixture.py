"""Local synthetic PDF fixture, including an optional valid large stream."""
from pathlib import Path


def make_book(path, megabytes=0):
    import fitz
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with fitz.open() as document:
        for number in range(1,11):
            page=document.new_page()
            unit='SECOND_UNIT_ADDITION' if number in (4,5) else 'OTHER_UNIT_CONTENT'
            page.insert_text((70,70),f'Physical page {number}. {unit}.')
            page.insert_text((70,100),'1 hour = ( ) minutes.')
        document.set_toc([[1,'封面',1],[1,'1 时分秒',2],[1,'2 万以内的加法和减法（一）',4],[1,'数字编码',6],[1,'3 测量',7],[1,'练习三',8],[1,'自我评价',9],[1,'后记',10]])
        document.save(path)
    if megabytes:
        from pypdf import PdfReader,PdfWriter
        from pypdf.generic import DecodedStreamObject,NameObject
        writer=PdfWriter(clone_from=PdfReader(path))
        stream=DecodedStreamObject();stream.set_data(b'A'*(megabytes*1024*1024))
        writer._root_object[NameObject('/TestPadding')]=writer._add_object(stream)
        output=path.with_suffix('.large.pdf')
        with output.open('wb') as file:writer.write(file)
        output.replace(path)
    return path


if __name__=='__main__':
    import sys
    make_book(sys.argv[1],int(sys.argv[2]) if len(sys.argv)>2 else 0)
