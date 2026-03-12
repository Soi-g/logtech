import zipfile, os
z = zipfile.ZipFile('/var/task/lambda_layer.zip', 'w', zipfile.ZIP_DEFLATED)
for r, d, files in os.walk('python'):
    for f in files:
        full = os.path.join(r, f)
        z.write(full, full.replace(os.sep, '/'))
z.close()
print('Done')
