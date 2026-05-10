FROM alpine

WORKDIR /some_test_data

RUN apk add --no-cache bash tzdata

COPY src/logger.sh /usr/local/bin/logger.sh
RUN chmod +x /usr/local/bin/logger.sh

CMD ["/usr/local/bin/logger.sh"]
