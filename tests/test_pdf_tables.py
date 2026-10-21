from io import BytesIO

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from agentai.core import document


def table_pdf():
    writer = PdfWriter()
    page = writer.add_blank_page(width=500, height=400)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
                             NameObject('/Subtype'): NameObject('/Type1'),
                             NameObject('/BaseFont'): NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):
        DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
    commands = ['BT /F1 12 Tf 20 350 Td (Report comparison) Tj ET']
    for x in (20, 160, 310, 480):
        commands.append(f'{x} 210 m {x} 300 l S')
    for y in (210, 240, 270, 300):
        commands.append(f'20 {y} m 480 {y} l S')
    rows = [('', 'SSRS', 'ISLO'),
            ('Controller', 'DefaultController', 'FormController'),
            ('Validation', 'ReportModel', '*Form::rules()')]
    for ri, row in enumerate(rows):
        for x, cell in zip((25, 165, 315), row):
            escaped = cell.replace('(', r'\(').replace(')', r'\)')
            commands.append(f'BT /F1 10 Tf {x} {282-ri*30} Td ({escaped}) Tj ET')
    commands.append('BT /F1 12 Tf 20 170 Td (End of comparison) Tj ET')
    stream = DecodedStreamObject()
    stream.set_data('\n'.join(commands).encode('ascii'))
    page[NameObject('/Contents')] = writer._add_object(stream)
    data = BytesIO()
    writer.write(data)
    return data.getvalue()


def test_pdf_table_keeps_each_value_attached_to_its_column():
    rows = list(document(table_pdf(), 'comparison.pdf', 'docs'))
    text = '\n'.join(row['text'] for row in rows)
    controller = next(line for line in text.splitlines() if 'DefaultController' in line)
    assert 'SSRS: DefaultController' in controller
    assert 'ISLO: FormController' in controller
    validation = next(line for line in text.splitlines() if 'ReportModel' in line)
    assert 'SSRS: ReportModel' in validation
    assert 'ISLO: *Form::rules()' in validation
    assert text.index('Report comparison') < text.index('DefaultController') < text.index('End of comparison')
    assert all('?page=1#L' in row['citation'] for row in rows)
