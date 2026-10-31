// Device creation policy only. No materials, draw state, shaders or animation changes.
#pragma once
#include <windows.h>
#include <d3d11.h>
#include <dxgi1_2.h>
#include <wrl/client.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>

namespace polar::graphics {
struct AdapterReport { std::string name,luid,note; };
// Initial device creation and the Host's observation run on that Host's own
// native worker. Separate menu owners must never read each other's identity.
inline AdapterReport& ownerReport(){static thread_local AdapterReport report;return report;}
inline AdapterReport adapterReport(){return ownerReport();}

inline HRESULT createDevice(IDXGIAdapter* original,D3D_DRIVER_TYPE driver,HMODULE software,
    UINT flags,const D3D_FEATURE_LEVEL* levels,UINT count,UINT sdk,
    ID3D11Device** device,D3D_FEATURE_LEVEL* level,ID3D11DeviceContext** context){
    using Microsoft::WRL::ComPtr;
    ComPtr<IDXGIAdapter1> chosen;
    std::string note;
    const char* requested=std::getenv("POLAR_RENDER_ADAPTER");
    if(!original&&driver==D3D_DRIVER_TYPE_HARDWARE&&requested&&std::strcmp(requested,"auto")){
        unsigned high{},low{};int used{};
        if(std::strlen(requested)==22&&std::sscanf(requested,"luid:%8x:%8x%n",&high,&low,&used)==2&&used==22){
            ComPtr<IDXGIFactory1> factory;
            if(SUCCEEDED(CreateDXGIFactory1(IID_PPV_ARGS(&factory)))){
                for(UINT i=0;i<32;++i){
                    ComPtr<IDXGIAdapter1> adapter;
                    if(FAILED(factory->EnumAdapters1(i,&adapter)))break;
                    DXGI_ADAPTER_DESC1 desc{};
                    if(SUCCEEDED(adapter->GetDesc1(&desc))&&!(desc.Flags&DXGI_ADAPTER_FLAG_SOFTWARE)&&
                        desc.AdapterLuid.LowPart==low&&unsigned(desc.AdapterLuid.HighPart)==high){chosen=adapter;break;}
                }
            }
            if(!chosen)note="Requested adapter unavailable; using system hardware default";
        }else note="Invalid adapter identifier; using system hardware default";
    }
    auto result=D3D11CreateDevice(chosen?chosen.Get():original,
        chosen?D3D_DRIVER_TYPE_UNKNOWN:driver,software,flags,levels,count,sdk,device,level,context);
    if(FAILED(result)&&chosen){
        char message[128];std::snprintf(message,sizeof(message),"Selected adapter creation failed (0x%08x); using system hardware default",unsigned(result));
        note=message;
        if(device&&*device){(*device)->Release();*device=nullptr;}
        if(context&&*context){(*context)->Release();*context=nullptr;}
        result=D3D11CreateDevice(original,driver,software,flags,levels,count,sdk,device,level,context);
    }
    AdapterReport observed;observed.note=note;
    if(SUCCEEDED(result)&&device&&*device){
        ComPtr<IDXGIDevice> dxgi;ComPtr<IDXGIAdapter> adapter;DXGI_ADAPTER_DESC desc{};
        if(SUCCEEDED((*device)->QueryInterface(IID_PPV_ARGS(&dxgi)))&&
            SUCCEEDED(dxgi->GetAdapter(&adapter))&&SUCCEEDED(adapter->GetDesc(&desc))){
            char name[512]{};WideCharToMultiByte(CP_UTF8,0,desc.Description,-1,name,sizeof(name),nullptr,nullptr);
            char luid[32];std::snprintf(luid,sizeof(luid),"luid:%08x:%08x",unsigned(desc.AdapterLuid.HighPart),unsigned(desc.AdapterLuid.LowPart));
            observed.name=name;observed.luid=luid;
        }
    }
    ownerReport()=observed;
    return result;
}
}
