FROM busybox:1.36
COPY index.html /www/index.html
CMD ["httpd", "-f", "-p", "8000", "-h", "/www"]
