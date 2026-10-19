CC ?= cc
PKG_CONFIG ?= pkg-config
AR ?= ar
CFLAGS ?= -O2 -g -Wall -Wextra -std=c11
CFLAGS += -D_POSIX_C_SOURCE=200809L -fPIC -Iinclude -Isrc $(shell $(PKG_CONFIG) --cflags libusb-1.0)
LDLIBS += $(shell $(PKG_CONFIG) --libs libusb-1.0) -lpthread -lm
CXX ?= c++
SOAPY_CXXFLAGS = $(shell $(PKG_CONFIG) --cflags SoapySDR)
SOAPY_LIBS = $(shell $(PKG_CONFIG) --libs SoapySDR)
SOAPY_MODULE_DIR ?= /usr/local/lib/$(shell $(CC) -dumpmachine)/SoapySDR/modules0.8

SRC = src/rsp.c src/msi2500.c src/msi001.c src/rsp1b_tables.c
OBJ = $(SRC:.c=.o)

all: librsp.a rsp-capture soapy

librsp.a: $(OBJ)
	$(AR) rcs $@ $^

rsp-capture: tools/rsp-capture.o librsp.a
	$(CC) $(CFLAGS) -o $@ tools/rsp-capture.o librsp.a $(LDLIBS)

soapy/SoapyRspOpen.o: soapy/SoapyRspOpen.cpp include/rsp.h src/rsp_internal.h
	$(CXX) -std=c++11 -O2 -g -Wall -Wextra -fPIC -Iinclude -Isrc \
		$(SOAPY_CXXFLAGS) -c -o $@ $<

soapy/libRspOpenSupport.so: soapy/SoapyRspOpen.o librsp.a
	$(CXX) -shared -o $@ soapy/SoapyRspOpen.o librsp.a $(SOAPY_LIBS) \
		-lusb-1.0 -lpthread -lm

soapy: soapy/libRspOpenSupport.so

install-soapy: soapy
	install -d $(DESTDIR)$(SOAPY_MODULE_DIR)
	install -m 755 soapy/libRspOpenSupport.so $(DESTDIR)$(SOAPY_MODULE_DIR)/

tests/test_tables: tests/test_tables.o librsp.a
	$(CC) $(CFLAGS) -o $@ tests/test_tables.o librsp.a $(LDLIBS)

test: tests/test_tables
	./tests/test_tables

clean:
	rm -f $(OBJ) tools/rsp-capture.o tests/test_tables.o soapy/SoapyRspOpen.o \
		librsp.a rsp-capture tests/test_tables soapy/libRspOpenSupport.so

.PHONY: all test clean soapy install-soapy
